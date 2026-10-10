"""
文档处理任务 - 解析 + 分块 + 向量化 + 写入 Qdrant

三档自适应处理策略（根据文件大小自动选择）：
┌──────────┬─────────────────────────────────────────────────────┐
│  小文件  │ 标准模式：全量解析 → 全部分块 → 全部嵌入 → 一次写入 │
│ (快速)   │ 优点：简单直接，开销小                              │
├──────────┼─────────────────────────────────────────────────────┤
│  中文件  │ Chunk流式：全量解析 → 流式分块 → 分批嵌入+写入      │
│ (平衡)   │ 优点：分块结果不驻留全内存                          │
├──────────┼─────────────────────────────────────────────────────┤
│  大文件  │ 页级流式：逐页解析 → 逐页分块 → 分批嵌入+写入       │
│ (低内存) │ 优点：解析阶段也不驻留全内存，峰值内存最低           │
└──────────┴─────────────────────────────────────────────────────┘

内存保护机制：
- MemoryGuard：实时监控系统内存，高压力时自动降低 batch_size
- 自动降级：页级流式是最后防线，32GB 内存也能处理 100MB+ PDF
"""

import asyncio
import os
import time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.chunkers.factory import TABULAR_FILE_TYPES, get_chunker, get_chunker_for_file
from app.chunkers.tabular_chunker import TabularChunker
from app.core.config import settings
from app.core.logging import get_logger
from app.models.db.document import ChunkDB, DocumentDB, DocumentVersionDB
from app.parsers.factory import get_total_pages, parse_file, parse_file_pages
from app.processors.base import BaseDocumentProcessor
from app.processors.factory import get_processor, is_supported
from app.providers.embedding.factory import get_embedding
from app.providers.storage.factory import get_storage
from app.repositories.qdrant_repo import get_qdrant
from app.services.task_service import TaskService, register_task
from app.utils.hash_utils import sha256_text
from app.utils.memory_utils import MemoryGuard, get_memory_usage_mb, get_system_memory_info
from app.utils.token_utils import count_tokens

logger = get_logger(__name__)


class StageTimer:
    """阶段计时器：方便打印各阶段耗时"""

    def __init__(self, label: str):
        self.label = label
        self.start_time = 0.0
        self.stages: list[tuple[str, float]] = []

    def start(self):
        self.start_time = time.perf_counter()

    def record(self, stage_name: str):
        elapsed = time.perf_counter() - self.start_time
        self.stages.append((stage_name, elapsed))
        mem_mb = get_memory_usage_mb()
        logger.info(
            f"[{self.label}] Stage '{stage_name}' completed in {elapsed:.2f}s, "
            f"memory: {mem_mb:.1f}MB"
        )
        self.start_time = time.perf_counter()
        return elapsed

    def summary(self) -> str:
        if not self.stages:
            return f"[{self.label}] No stages recorded"
        total = sum(t for _, t in self.stages)
        lines = [f"[{self.label}] Total: {total:.2f}s"]
        for name, t in self.stages:
            pct = (t / total * 100) if total > 0 else 0
            lines.append(f"  {name}: {t:.2f}s ({pct:.1f}%)")
        return "\n".join(lines)


# ---------- 阈值配置（从 settings 读取，有默认值） ----------
def _get_streaming_threshold() -> int:
    """中文件阈值：超过此字符数启用 chunk 级流式"""
    return getattr(settings, "streaming_threshold_chars", 500_000)


def _get_page_streaming_threshold_pages() -> int:
    """大文件页数阈值：超过此页数启用页级流式"""
    return getattr(settings, "page_streaming_threshold_pages", 200)


def _get_page_streaming_file_size() -> int:
    """大文件大小阈值（字节）：超过此大小启用页级流式，默认 10MB"""
    return getattr(settings, "page_streaming_file_size_mb", 10) * 1024 * 1024


def _get_streaming_batch_size() -> int:
    """流式处理每批 chunk 数"""
    return getattr(settings, "streaming_batch_size", 200)


def _get_qdrant_batch_size() -> int:
    """Qdrant 每批写入的 point 数"""
    return getattr(settings, "qdrant_upsert_batch_size", 500)


def _get_text_length_class(text_len: int) -> str:
    """根据文本长度分类"""
    if text_len < 100_000:
        return "small"
    elif text_len < 500_000:
        return "medium"
    elif text_len < 5_000_000:
        return "large"
    elif text_len < 50_000_000:
        return "xlarge"
    else:
        return "xxlarge"


def _is_tabular_file(file_type: str) -> bool:
    """判断是否为表格类文件（应使用 TabularChunker）"""
    if not file_type:
        return False
    return file_type.lower() in TABULAR_FILE_TYPES


def _should_use_tabular_chunker(file_type: str, parse_result=None) -> bool:
    """
    判断是否应使用表格切片器。

    条件：
    1. 文件类型是表格类
    2. 配置允许自动使用表格切片器
    3.（可选）解析结果中包含表格数据
    """
    if not _is_tabular_file(file_type):
        return False
    if not getattr(settings, "tabular_auto_for_spreadsheets", True):
        return False
    if parse_result is not None:
        # 如果有解析结果，确保它有表格数据
        tables = getattr(parse_result, "tables", None)
        if tables is not None and len(tables) > 0:
            # 至少有一个表有数据
            return any(len(t) >= 2 for t in tables)
    return True


def _estimate_tabular_chunks(parse_result, chunker: TabularChunker) -> int:
    """估算表格文件的 chunk 总数"""
    tables = getattr(parse_result, "tables", []) or []
    total = 0
    for table in tables:
        if len(table) < 2:
            continue
        data_rows = len(table) - 1
        headers = table[0]
        if chunker.mode == "row":
            total += data_rows
        elif chunker.mode == "row_group":
            rows_per = chunker.rows_per_chunk or chunker._estimate_rows_per_chunk(headers)
            total += max(1, data_rows // max(1, rows_per))
        elif chunker.mode == "column_group":
            cols_per = chunker.columns_per_group or 10
            col_groups = max(1, len(headers) // max(1, cols_per))
            total += col_groups
        else:
            total += data_rows
    return max(1, total)


def _is_record_mode(file_type: str, processor: BaseDocumentProcessor | None = None) -> bool:
    """
    判断表格文件是否为 record 模式。

    Record 模式下，除了写 chunks 表和 Qdrant，
    还需要写 document_records 表（结构化数据，用于 SmartQuery 精确查询）。
    """
    if not _is_tabular_file(file_type):
        return False

    # 优先从 processor 获取模式
    if processor is not None and hasattr(processor, "_get_mode"):
        try:
            return processor._get_mode() == "record"
        except Exception:
            pass

    # 回退：从配置读取
    from app.core.config import get_tabular_config

    try:
        cfg = get_tabular_config(file_type)
        return cfg.get("mode", "record") == "record"
    except Exception:
        return False


async def _import_records_for_document(
    db: AsyncSession,
    file_path: str,
    document_id: str,
    version_id: str,
    collection_id: str,
    file_type: str,
) -> int:
    """
    将 Excel 数据导入为结构化记录（document_records 表）。

    仅在 record 模式下调用。
    使用 DocumentRecordService 流式写入 PostgreSQL。
    """
    try:
        from app.services.document_record_service import (
            DocumentRecordService,
            RecordImportConsistencyError,
        )

        record_svc = DocumentRecordService(db=db, collection_id=collection_id)
        total = await record_svc.import_excel(
            file_path=file_path,
            document_id=document_id,
            version_id=version_id,
            batch_size=500,
        )
        logger.info(f"Record import complete: {total} records for doc {document_id}")
        return total
    except RecordImportConsistencyError:
        # 静默丢行属于数据完整性问题：不能让任务"假成功"，
        # 直接上抛让任务失败（上层会 rollback 未提交数据并标记失败/重试）
        logger.error("Record import consistency check failed; failing the task", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Failed to import records: {e}", exc_info=True)
        return 0


@register_task("document_process")
async def process_document_task(payload: dict, task_id: str, task_svc: TaskService) -> dict:
    """
    文档处理任务：解析 → 分块 → 向量化 → 写入 Qdrant
    根据文件大小自动选择处理模式。
    """
    version_id = payload["version_id"]
    document_id = payload["document_id"]
    collection_id = payload["collection_id"]
    file_type = payload["file_type"]

    db: AsyncSession = task_svc.db
    qdrant = get_qdrant()
    storage = get_storage()

    # 1. 获取版本信息
    result = await db.execute(select(DocumentVersionDB).where(DocumentVersionDB.id == version_id))
    version = result.scalar_one_or_none()
    if not version:
        raise ValueError(f"Version not found: {version_id}")

    # 2. 获取文件本地路径
    file_path = storage.get_local_path(version.storage_path)
    tmp_file_path = None
    if not file_path:
        data = await storage.download(version.storage_path)
        import tempfile

        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{file_type}")
        tmp.write(data)
        tmp.close()
        file_path = tmp.name
        tmp_file_path = tmp.name

    processor = None
    cancelled = False

    try:
        # 3. 维度一致性检查：确保 Qdrant 集合维度与当前 embedding 模型匹配
        #    如果维度不一致，自动重建集合（旧向量会被清空，后面重新写入）
        await _ensure_collection_dimension(
            db=db,
            qdrant=qdrant,
            collection_id=collection_id,
            version=version,
        )

        # 4. 决定处理模式 —— 用 processor 统一获取文件信息
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0

        # 通过 processor 获取文件元信息（页数、估算 chunks 等）
        proc_info = None
        if is_supported(file_type):
            processor = get_processor(file_type)
            try:
                proc_info = processor.get_info(file_path)
            except Exception as e:
                logger.debug(f"Processor get_info failed, will use fallback: {e}")
                proc_info = None

        # 回退：用老的 get_total_pages 方式
        if proc_info and proc_info.total_pages > 0:
            total_pages = proc_info.total_pages
        else:
            total_pages = get_total_pages(file_path, file_type)

        use_page_streaming = (
            total_pages >= _get_page_streaming_threshold_pages()
            or file_size >= _get_page_streaming_file_size()
        )

        # 记录系统内存信息
        sys_mem = get_system_memory_info()
        if sys_mem:
            logger.info(
                f"System memory: total={sys_mem['total_gb']}GB, "
                f"available={sys_mem['available_gb']}GB, "
                f"used={sys_mem['used_percent']:.1f}%"
            )

        logger.info(
            f"File: {file_type}, size={file_size / 1024 / 1024:.1f}MB, "
            f"pages={total_pages}, mode={'page-streaming' if use_page_streaming else 'decide-after-parse'}"
        )

        chunk_count = 0

        if use_page_streaming:
            # 大文件：页级流式处理（内存最优）
            logger.info("Processing mode: PAGE-STREAMING (memory-optimized for large files)")
            chunk_count = await _process_page_streaming(
                db=db,
                qdrant=qdrant,
                task_id=task_id,
                task_svc=task_svc,
                file_path=file_path,
                file_type=file_type,
                version=version,
                document_id=document_id,
                collection_id=collection_id,
                total_pages=total_pages,
                file_size=file_size,
                processor=processor,
            )
        else:
            # 中小文件：先解析再决定
            await task_svc.check_cancelled(task_id)
            await task_svc.update_progress(task_id, 0.1, {"stage": "parsing"})

            # ===== Excel 性能剖析：解析阶段计时 =====
            t_parse_start = time.perf_counter()
            logger.info(f"[Excel-Perf] [{task_id}] Stage 1 - Parsing start: {file_path}")
            parse_result = await parse_file(file_path, file_type)
            t_parse = time.perf_counter() - t_parse_start
            tables_count = len(parse_result.tables) if parse_result.tables else 0
            total_rows = sum(len(t) for t in parse_result.tables) if parse_result.tables else 0
            logger.info(
                f"[Excel-Perf] [{task_id}] Stage 1 - Parsing done: {t_parse:.2f}s, "
                f"sheets={tables_count}, total_rows={total_rows}, "
                f"text_len={len(parse_result.text)}"
            )
            # ==========================================

            text_len = len(parse_result.text)
            page_count = len(parse_result.pages)
            size_class = _get_text_length_class(text_len)

            logger.info(
                f"Parsed document, text length: {text_len} ({size_class}), pages: {page_count}"
            )

            if text_len == 0:
                version.status = "completed"
                version.chunk_count = 0
                await db.commit()
                return {"chunk_count": 0}

            use_chunk_streaming = text_len > _get_streaming_threshold()

            if use_chunk_streaming:
                # 中文件：chunk 级流式
                logger.info("Processing mode: CHUNK-STREAMING (balanced)")
                chunk_count = await _process_chunk_streaming(
                    db=db,
                    qdrant=qdrant,
                    task_id=task_id,
                    task_svc=task_svc,
                    parse_result=parse_result,
                    version=version,
                    document_id=document_id,
                    collection_id=collection_id,
                    file_type=file_type,
                    text_len=text_len,
                    file_path=file_path,
                    processor=processor,
                )
            else:
                # 小文件：标准模式
                logger.info("Processing mode: STANDARD (small file, fastest)")
                chunk_count = await _process_standard(
                    db=db,
                    qdrant=qdrant,
                    task_id=task_id,
                    task_svc=task_svc,
                    parse_result=parse_result,
                    version=version,
                    document_id=document_id,
                    collection_id=collection_id,
                    file_type=file_type,
                    file_path=file_path,
                    processor=processor,
                )

        # 4. 后处理
        await _finalize_document(
            db=db,
            qdrant=qdrant,
            version=version,
            document_id=document_id,
            collection_id=collection_id,
            chunk_count=chunk_count,
            task_id=task_id,
            task_svc=task_svc,
        )

        final_mem = get_memory_usage_mb()
        logger.info(f"Processing complete. Final memory usage: {final_mem:.1f}MB")

        await task_svc.update_progress(task_id, 1.0, {"stage": "completed"})

        return {
            "chunk_count": chunk_count,
            "version_id": version_id,
            "document_id": document_id,
        }

    except asyncio.CancelledError:
        cancelled = True
        logger.warning(f"Task {task_id} was cancelled, cleaning up partial data...")
        # 任务被取消，清理已入库的部分数据
        await _cleanup_cancelled_task(
            db=db,
            qdrant=qdrant,
            collection_id=collection_id,
            version_id=version_id,
            document_id=document_id,
            task_id=task_id,
        )
        raise

    finally:
        # 释放 processor 资源（每个请求一个独立实例，用完即关）
        if processor is not None:
            try:
                processor.close()
            except Exception:
                logger.debug("Failed to close processor", exc_info=True)

        # 清理临时文件
        if tmp_file_path and os.path.exists(tmp_file_path):
            try:
                os.unlink(tmp_file_path)
            except Exception:
                pass

        # 如果是被取消的，更新版本状态为 cancelled
        if cancelled:
            try:
                version.status = "cancelled"
                await db.commit()
            except Exception:
                logger.debug(f"Failed to update version status for cancelled task {task_id}")


# ------------------------------------------------------------------
# 模式一：标准模式（小文件）
# ------------------------------------------------------------------
async def _process_standard(
    db: AsyncSession,
    qdrant,
    task_id: str,
    task_svc: TaskService,
    parse_result,
    version: DocumentVersionDB,
    document_id: str,
    collection_id: str,
    file_type: str,
    file_path: str | None = None,
    processor: BaseDocumentProcessor | None = None,
) -> int:
    """标准模式：一次性分块、向量化、写入"""
    timer = StageTimer("STANDARD")
    timer.start()

    is_excel = _is_tabular_file(file_type)

    await task_svc.check_cancelled(task_id)
    await task_svc.update_progress(task_id, 0.15, {"stage": "chunking", "mode": "standard"})

    # ===== Excel 性能剖析：切片阶段计时 =====
    if is_excel:
        t_chunk_start = time.perf_counter()
        logger.info(f"[Excel-Perf] [{task_id}] Stage 2 - Chunking start (standard mode)")
    # ==========================================

    # ---- 优先使用 processor 做切片（统一入口） ----
    if processor and file_path:
        chunk_items = await processor.chunk(file_path)
    else:
        # ---- 回退：旧的 parser + chunker 组合 ----
        if _should_use_tabular_chunker(file_type, parse_result):
            chunker = get_chunker_for_file(file_type)
            if isinstance(chunker, TabularChunker):
                sheet_names = parse_result.metadata.get("sheets") if parse_result.metadata else None
                chunk_items = chunker.chunk_tables(
                    tables=parse_result.tables,
                    sheet_names=sheet_names,
                    file_path=file_path,
                    file_type=file_type,
                )
                chunker.close()
            else:
                if parse_result.pages and len(parse_result.pages) > 1:
                    chunk_items = chunker.chunk_with_pages(parse_result.pages)
                else:
                    chunk_items = chunker.chunk(parse_result.text)
        else:
            chunker = get_chunker()
            if parse_result.pages and len(parse_result.pages) > 1:
                chunk_items = chunker.chunk_with_pages(parse_result.pages)
            else:
                chunk_items = chunker.chunk(parse_result.text)

    timer.record("chunking")
    logger.info(f"Chunked into {len(chunk_items)} chunks")

    # ===== Excel Record 模式：写入 document_records 表 =====
    record_count = 0
    if is_excel and _is_record_mode(file_type, processor) and file_path:
        await task_svc.update_progress(task_id, 0.2, {"stage": "importing_records"})
        t_rec_start = time.perf_counter()
        logger.info(f"[Excel-Perf] [{task_id}] Stage 2.5 - Record import start")
        record_count = await _import_records_for_document(
            db=db,
            file_path=file_path,
            document_id=document_id,
            version_id=version.id,
            collection_id=collection_id,
            file_type=file_type,
        )
        t_rec = time.perf_counter() - t_rec_start
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 2.5 - Record import done: "
            f"{t_rec:.2f}s, records={record_count}"
        )
    # =========================================================

    # ===== Excel 性能剖析：切片阶段完成 =====
    if is_excel:
        t_chunk = time.perf_counter() - t_chunk_start
        avg_chunk_len = (
            sum(len(c.text) for c in chunk_items) / len(chunk_items) if chunk_items else 0
        )
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 2 - Chunking done: {t_chunk:.2f}s, "
            f"chunks={len(chunk_items)}, avg_chunk_len={avg_chunk_len:.0f} chars"
        )
    # ==========================================

    if not chunk_items:
        return 0

    await task_svc.check_cancelled(task_id)
    await task_svc.update_progress(
        task_id,
        0.3,
        {
            "stage": "embedding",
            "total": len(chunk_items),
            "mode": "standard",
        },
    )

    # ===== Excel 性能剖析：嵌入阶段计时 =====
    if is_excel:
        t_emb_start = time.perf_counter()
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 3 - Embedding start: {len(chunk_items)} chunks"
        )
    # ==========================================

    embedding = await get_embedding()
    timer.record("embedding_setup")

    texts = [c.text for c in chunk_items]
    vectors = await embedding.embed_texts(texts)

    timer.record("embedding")
    logger.info(f"Embedding complete: {len(vectors)} vectors")

    # ===== Excel 性能剖析：嵌入阶段完成 =====
    if is_excel:
        t_emb = time.perf_counter() - t_emb_start
        emb_speed = len(chunk_items) / t_emb if t_emb > 0 else 0
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 3 - Embedding done: {t_emb:.2f}s, "
            f"speed={emb_speed:.1f} chunks/s, avg_len={sum(len(t) for t in texts) / len(texts) if texts else 0:.0f} chars"
        )
    # ==========================================

    await task_svc.check_cancelled(task_id)
    await task_svc.update_progress(task_id, 0.7, {"stage": "saving_chunks"})

    # ===== Excel 性能剖析：DB 保存阶段 =====
    if is_excel:
        t_db_start = time.perf_counter()
        logger.info(f"[Excel-Perf] [{task_id}] Stage 4 - DB save start: {len(chunk_items)} chunks")
    # ==========================================

    chunk_objs = []
    for i, (item, vec) in enumerate(zip(chunk_items, vectors)):
        chunk = ChunkDB(
            version_id=version.id,
            document_id=document_id,
            chunk_index=item.index or i,
            text=item.text,
            content_hash=sha256_text(item.text),
            page_num=item.page_num,
            char_start=item.char_start,
            char_end=item.char_end,
            token_count=count_tokens(item.text),
        )
        db.add(chunk)
        chunk_objs.append(chunk)

    await db.flush()
    chunk_ids = [c.id for c in chunk_objs]
    timer.record("db_save")

    # ===== Excel 性能剖析：DB 保存完成 =====
    if is_excel:
        t_db = time.perf_counter() - t_db_start
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 4 - DB save done: {t_db:.2f}s, "
            f"chunks={len(chunk_objs)}"
        )
    # ==========================================

    await task_svc.check_cancelled(task_id)
    await task_svc.update_progress(task_id, 0.85, {"stage": "writing_qdrant"})

    result = await db.execute(select(DocumentDB).where(DocumentDB.id == document_id))
    doc = result.scalar_one()

    points = _build_qdrant_points(
        chunk_items=chunk_items,
        chunk_ids=chunk_ids,
        vectors=vectors,
        document_id=document_id,
        version_id=version.id,
        collection_id=collection_id,
        directory_id=doc.directory_id or "",
        file_name=doc.name,
        file_type=file_type,
        created_at=version.created_at.isoformat() if version.created_at else "",
    )

    # ===== Excel 性能剖析：Qdrant 写入阶段 =====
    if is_excel:
        t_qdrant_start = time.perf_counter()
        logger.info(f"[Excel-Perf] [{task_id}] Stage 5 - Qdrant write start: {len(points)} points")
    # ==========================================

    await qdrant.upsert_points(collection_id, points, batch_size=_get_qdrant_batch_size())
    timer.record("qdrant_write")

    # ===== Excel 性能剖析：Qdrant 写入完成 =====
    if is_excel:
        t_qdrant = time.perf_counter() - t_qdrant_start
        qdrant_speed = len(points) / t_qdrant if t_qdrant > 0 else 0
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 5 - Qdrant write done: {t_qdrant:.2f}s, "
            f"points={len(points)}, speed={qdrant_speed:.1f} points/s, "
            f"batch_size={_get_qdrant_batch_size()}"
        )
    # ==========================================

    logger.info(timer.summary())
    return len(chunk_items)


# ------------------------------------------------------------------
# 模式二：Chunk 级流式（中文件）
# ------------------------------------------------------------------
async def _process_chunk_streaming(
    db: AsyncSession,
    qdrant,
    task_id: str,
    task_svc: TaskService,
    parse_result,
    version: DocumentVersionDB,
    document_id: str,
    collection_id: str,
    file_type: str,
    text_len: int,
    file_path: str | None = None,
    processor: BaseDocumentProcessor | None = None,
) -> int:
    """Chunk 级流式：分块结果不驻留内存，分批嵌入和写入"""
    timer = StageTimer("CHUNK-STREAMING")
    timer.start()

    is_excel = _is_tabular_file(file_type)

    embedding = await get_embedding()
    timer.record("setup")

    result = await db.execute(select(DocumentDB).where(DocumentDB.id == document_id))
    doc = result.scalar_one()

    # ---- 优先使用 processor 流式切片 ----
    use_processor_stream = processor and file_path

    if use_processor_stream:
        info = processor.get_info(file_path)
        est_total_chunks = info.estimated_chunks if info.estimated_chunks > 0 else 100
        chunk_iter = processor.chunk_stream(file_path)
        is_async_iter = True
        logger.info(
            f"Using {type(processor).__name__} streaming, estimated ~{est_total_chunks} chunks"
        )
    else:
        # ---- 回退：旧的方式 ----
        use_tabular = _should_use_tabular_chunker(file_type, parse_result)
        if use_tabular:
            chunker = get_chunker_for_file(file_type)
        else:
            chunker = get_chunker()

        if use_tabular and isinstance(chunker, TabularChunker):
            sheet_names = parse_result.metadata.get("sheets") if parse_result.metadata else None
            est_total_chunks = _estimate_tabular_chunks(parse_result, chunker)
            chunk_iter = chunker.chunk_tables_stream(
                tables=parse_result.tables,
                sheet_names=sheet_names,
                file_path=file_path,
                file_type=file_type,
            )
            is_async_iter = False
            logger.info(
                f"Using TabularChunker for {file_type} file, "
                f"mode={chunker.mode}, estimated ~{est_total_chunks} chunks"
            )
        else:
            est_total_chunks = max(1, text_len // (settings.chunk_size - settings.chunk_overlap))
            if parse_result.pages and len(parse_result.pages) > 1:
                chunk_iter = chunker.chunk_stream_with_pages(parse_result.pages)
            else:
                chunk_iter = chunker.chunk_stream(parse_result.text)
            is_async_iter = False

    # ===== Excel 性能剖析：流式切片开始 =====
    if is_excel:
        t_chunk_stream_start = time.perf_counter()
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 2 - Chunk streaming start "
            f"(chunk-streaming mode), estimated ~{est_total_chunks} chunks"
        )
    # ==========================================

    # ===== Excel Record 模式：流式处理前先导入 records =====
    record_count = 0
    if is_excel and _is_record_mode(file_type, processor) and file_path:
        await task_svc.update_progress(task_id, 0.15, {"stage": "importing_records"})
        t_rec_start = time.perf_counter()
        logger.info(f"[Excel-Perf] [{task_id}] Stage 2.1 - Record import start (chunk-streaming)")
        record_count = await _import_records_for_document(
            db=db,
            file_path=file_path,
            document_id=document_id,
            version_id=version.id,
            collection_id=collection_id,
            file_type=file_type,
        )
        t_rec = time.perf_counter() - t_rec_start
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 2.1 - Record import done: "
            f"{t_rec:.2f}s, records={record_count}"
        )
    # ==========================================================

    # 内存卫士：自动调整 batch_size
    batch_size = _get_streaming_batch_size()
    mem_guard = MemoryGuard(initial_batch=batch_size, min_batch=16)

    batch_chunks = []
    chunk_count = 0
    batch_index = 0
    emb_start = 0.2
    emb_end = 0.8

    # 流式处理统计
    total_chunk_gen_time = 0.0
    total_embedding_time = 0.0
    total_db_time = 0.0
    total_qdrant_time = 0.0
    _last_chunk_time = time.perf_counter()

    # 统一处理：async generator 和 sync generator 都走这条链路
    # 用 _iter_chunks 辅助函数统一成 async for
    async def _iter_chunks():
        if is_async_iter:
            async for c in chunk_iter:
                yield c
        else:
            for c in chunk_iter:
                yield c

    async for chunk_item in _iter_chunks():
        batch_chunks.append(chunk_item)

        if len(batch_chunks) >= mem_guard.current_batch_size:
            await task_svc.check_cancelled(task_id)

            # 计算切片生成时间
            chunk_gen_time = time.perf_counter() - _last_chunk_time
            total_chunk_gen_time += chunk_gen_time

            t0 = time.perf_counter()
            batch_result = await _process_one_batch(
                db=db,
                qdrant=qdrant,
                batch_chunks=batch_chunks,
                embedding=embedding,
                version=version,
                document_id=document_id,
                collection_id=collection_id,
                directory_id=doc.directory_id or "",
                file_name=doc.name,
                file_type=file_type,
                created_at=version.created_at.isoformat() if version.created_at else "",
                batch_index=batch_index,
                return_timings=True,
            )
            processed = batch_result.get("count", len(batch_chunks))
            chunk_count += processed
            batch_time = time.perf_counter() - t0

            # 累计各阶段时间
            total_embedding_time += batch_result.get("embedding_time", 0)
            total_db_time += batch_result.get("db_time", 0)
            total_qdrant_time += batch_result.get("qdrant_time", 0)

            # 调整内存
            mem_guard.check_and_adjust()

            # 进度：基于已处理 chunk 数 / 估算总 chunk 数
            ratio = min(1.0, chunk_count / max(est_total_chunks, chunk_count))
            progress = emb_start + ratio * (emb_end - emb_start)
            progress = min(emb_end, progress)

            # 计算速度（chunks/秒）
            speed = processed / batch_time if batch_time > 0 else 0

            await task_svc.update_progress(
                task_id,
                progress,
                {
                    "stage": "embedding",
                    "processed": chunk_count,
                    "estimated_total": est_total_chunks,
                    "batch": batch_index,
                    "batch_size": mem_guard.current_batch_size,
                    "speed_chunks_s": round(speed, 1),
                    "memory_mb": round(get_memory_usage_mb(), 1),
                },
            )

            # 每 10 批打一条详细日志（Excel 文件）
            if is_excel and (batch_index % 10 == 0 or batch_index == 0):
                logger.info(
                    f"[Excel-Perf] [{task_id}] Batch {batch_index}: "
                    f"{chunk_count}/{est_total_chunks} chunks, "
                    f"speed={speed:.1f} chunks/s, "
                    f"batch_size={mem_guard.current_batch_size}, "
                    f"chunk_gen={chunk_gen_time:.2f}s, "
                    f"embedding={batch_result.get('embedding_time', 0):.2f}s, "
                    f"db={batch_result.get('db_time', 0):.2f}s, "
                    f"qdrant={batch_result.get('qdrant_time', 0):.2f}s, "
                    f"mem={get_memory_usage_mb():.1f}MB"
                )
            elif batch_index % 10 == 0:
                logger.info(
                    f"Chunk streaming batch {batch_index}: "
                    f"{chunk_count}/{est_total_chunks} chunks, "
                    f"speed={speed:.1f} chunks/s, "
                    f"batch_size={mem_guard.current_batch_size}, "
                    f"mem={get_memory_usage_mb():.1f}MB"
                )

            batch_chunks.clear()
            batch_index += 1
            _last_chunk_time = time.perf_counter()
            await db.commit()

    if batch_chunks:
        await task_svc.check_cancelled(task_id)

        chunk_gen_time = time.perf_counter() - _last_chunk_time
        total_chunk_gen_time += chunk_gen_time

        batch_result = await _process_one_batch(
            db=db,
            qdrant=qdrant,
            batch_chunks=batch_chunks,
            embedding=embedding,
            version=version,
            document_id=document_id,
            collection_id=collection_id,
            directory_id=doc.directory_id or "",
            file_name=doc.name,
            file_type=file_type,
            created_at=version.created_at.isoformat() if version.created_at else "",
            batch_index=batch_index,
            return_timings=True,
        )
        chunk_count += batch_result.get("count", len(batch_chunks))
        total_embedding_time += batch_result.get("embedding_time", 0)
        total_db_time += batch_result.get("db_time", 0)
        total_qdrant_time += batch_result.get("qdrant_time", 0)
        await db.commit()

    timer.record("embedding_and_write")

    # ===== Excel 性能剖析：流式处理汇总 =====
    if is_excel:
        t_total = time.perf_counter() - t_chunk_stream_start
        total_pipeline = total_embedding_time + total_db_time + total_qdrant_time
        logger.info(
            f"[Excel-Perf] [{task_id}] Stage 2-5 - Chunk streaming summary: "
            f"total={t_total:.2f}s, chunks={chunk_count}, batches={batch_index + 1}\n"
            f"  - Chunk generation: {total_chunk_gen_time:.2f}s "
            f"({total_chunk_gen_time / t_total * 100:.1f}%)\n"
            f"  - Embedding: {total_embedding_time:.2f}s "
            f"({total_embedding_time / t_total * 100:.1f}%), "
            f"speed={chunk_count / total_embedding_time if total_embedding_time > 0 else 0:.1f} chunks/s\n"
            f"  - DB save: {total_db_time:.2f}s "
            f"({total_db_time / t_total * 100:.1f}%)\n"
            f"  - Qdrant write: {total_qdrant_time:.2f}s "
            f"({total_qdrant_time / t_total * 100:.1f}%), "
            f"speed={chunk_count / total_qdrant_time if total_qdrant_time > 0 else 0:.1f} points/s"
        )
    # ==========================================

    await task_svc.update_progress(
        task_id,
        0.85,
        {
            "stage": "finalizing",
            "total": chunk_count,
        },
    )

    logger.info(f"Chunk streaming complete: {chunk_count} chunks in {batch_index + 1} batches")
    logger.info(timer.summary())
    return chunk_count


# ------------------------------------------------------------------
# 模式三：页级流式（大文件，内存最优）
# ------------------------------------------------------------------
async def _process_page_streaming(
    db: AsyncSession,
    qdrant,
    task_id: str,
    task_svc: TaskService,
    file_path: str,
    file_type: str,
    version: DocumentVersionDB,
    document_id: str,
    collection_id: str,
    total_pages: int,
    file_size: int,
    processor: BaseDocumentProcessor | None = None,
) -> int:
    """
    页级流式处理：逐页解析 → 逐页分块 → 攒够一批就嵌入+写入 → 释放

    内存峰值仅取决于：
    - 当前正在处理的几页文本
    - 当前 batch 的 chunk + 向量
    与总文件大小几乎无关。

    这是 100MB+ PDF 的推荐处理模式。
    对于表格文件（Excel/CSV），改用 processor 流式切片。
    """
    embedding = await get_embedding()

    result = await db.execute(select(DocumentDB).where(DocumentDB.id == document_id))
    doc = result.scalar_one()

    # 内存卫士
    batch_size = _get_streaming_batch_size()
    mem_guard = MemoryGuard(initial_batch=batch_size, min_batch=8)

    batch_chunks = []
    chunk_count = 0
    global_chunk_index = 0  # 全局 chunk 索引，避免每页从 0 开始导致唯一键冲突
    pages_processed = 0
    batch_index = 0  # 批次序号，用于日志标识
    created_at = version.created_at.isoformat() if version.created_at else ""

    # 进度范围：0.1 ~ 0.85（解析+嵌入+写入都在流式里）
    prog_start = 0.1
    prog_end = 0.85

    # ---- 表格文件 / 有 processor：使用 processor 统一处理 ----
    if processor is not None:
        is_excel = _is_tabular_file(file_type)
        logger.info(f"Using {type(processor).__name__} in page-streaming mode")

        proc_info = processor.get_info(file_path)
        est_total = proc_info.estimated_chunks if proc_info.estimated_chunks > 0 else 100

        # ===== Excel Record 模式：先导入 records =====
        record_count = 0
        if is_excel and _is_record_mode(file_type, processor) and file_path:
            await task_svc.update_progress(task_id, 0.1, {"stage": "importing_records"})
            t_rec_start = time.perf_counter()
            logger.info(f"[Excel-Perf] [{task_id}] Page-streaming - Record import start")
            record_count = await _import_records_for_document(
                db=db,
                file_path=file_path,
                document_id=document_id,
                version_id=version.id,
                collection_id=collection_id,
                file_type=file_type,
            )
            t_rec = time.perf_counter() - t_rec_start
            logger.info(
                f"[Excel-Perf] [{task_id}] Page-streaming - Record import done: "
                f"{t_rec:.2f}s, records={record_count}"
            )
        # ==============================================

        # ===== Excel 性能剖析：页级流式开始 =====
        if is_excel:
            t_page_stream_start = time.perf_counter()
            total_emb = 0.0
            total_db = 0.0
            total_qdrant = 0.0
            total_chunk_gen = 0.0
            _last_chunk_time = time.perf_counter()
            logger.info(
                f"[Excel-Perf] [{task_id}] Page-streaming mode start, estimated ~{est_total} chunks"
            )
        # ==========================================

        async for chunk_item in processor.chunk_stream(file_path):
            await task_svc.check_cancelled(task_id)
            # 全局 index
            chunk_item.index = global_chunk_index
            global_chunk_index += 1

            batch_chunks.append(chunk_item)

            # 攒够一批就处理
            if len(batch_chunks) >= mem_guard.current_batch_size:
                if is_excel:
                    chunk_gen_time = time.perf_counter() - _last_chunk_time
                    total_chunk_gen += chunk_gen_time

                batch_result = await _process_one_batch(
                    db=db,
                    qdrant=qdrant,
                    batch_chunks=batch_chunks,
                    embedding=embedding,
                    version=version,
                    document_id=document_id,
                    collection_id=collection_id,
                    directory_id=doc.directory_id or "",
                    file_name=doc.name,
                    file_type=file_type,
                    created_at=created_at,
                    batch_index=batch_index,
                    return_timings=is_excel,
                )
                if is_excel:
                    processed = batch_result.get("count", len(batch_chunks))
                    chunk_count += processed
                    total_emb += batch_result.get("embedding_time", 0)
                    total_db += batch_result.get("db_time", 0)
                    total_qdrant += batch_result.get("qdrant_time", 0)
                else:
                    chunk_count += batch_result

                mem_guard.check_and_adjust()
                batch_chunks.clear()
                batch_index += 1
                await db.commit()

                # 更新进度
                ratio = min(1.0, chunk_count / max(est_total, chunk_count))
                progress = prog_start + ratio * (prog_end - prog_start)
                progress = min(prog_end, progress)
                await task_svc.update_progress(
                    task_id,
                    progress,
                    {
                        "stage": f"processing_{file_type}",
                        "chunks": chunk_count,
                        "estimated_total": est_total,
                        "processor": type(processor).__name__,
                        "batch_size": mem_guard.current_batch_size,
                        "memory_mb": round(get_memory_usage_mb(), 1),
                    },
                )

                # 每 10 批打一条详细日志
                if is_excel and batch_index % 10 == 0:
                    batch_time = (
                        batch_result.get("total_time", 0) if isinstance(batch_result, dict) else 0
                    )
                    speed = (batch_result.get("count", 0) / batch_time) if batch_time > 0 else 0
                    logger.info(
                        f"[Excel-Perf] [{task_id}] Page-stream batch {batch_index}: "
                        f"{chunk_count}/{est_total} chunks, "
                        f"speed={speed:.1f} chunks/s, "
                        f"batch_size={mem_guard.current_batch_size}, "
                        f"emb={batch_result.get('embedding_time', 0):.2f}s, "
                        f"db={batch_result.get('db_time', 0):.2f}s, "
                        f"qdrant={batch_result.get('qdrant_time', 0):.2f}s"
                    )

                if is_excel:
                    _last_chunk_time = time.perf_counter()

        # 处理最后一批
        if batch_chunks:
            batch_result = await _process_one_batch(
                db=db,
                qdrant=qdrant,
                batch_chunks=batch_chunks,
                embedding=embedding,
                version=version,
                document_id=document_id,
                collection_id=collection_id,
                directory_id=doc.directory_id or "",
                file_name=doc.name,
                file_type=file_type,
                created_at=created_at,
                batch_index=batch_index,
                return_timings=is_excel,
            )
            if is_excel:
                chunk_count += batch_result.get("count", len(batch_chunks))
                total_emb += batch_result.get("embedding_time", 0)
                total_db += batch_result.get("db_time", 0)
                total_qdrant += batch_result.get("qdrant_time", 0)
            else:
                chunk_count += batch_result
            await db.commit()

        processor.close()

        # ===== Excel 性能剖析：页级流式汇总 =====
        if is_excel:
            t_total = time.perf_counter() - t_page_stream_start
            logger.info(
                f"[Excel-Perf] [{task_id}] Page-streaming summary: "
                f"total={t_total:.2f}s, chunks={chunk_count}, batches={batch_index + 1}\n"
                f"  - Chunk generation: {total_chunk_gen:.2f}s "
                f"({total_chunk_gen / t_total * 100:.1f}%)\n"
                f"  - Embedding: {total_emb:.2f}s "
                f"({total_emb / t_total * 100:.1f}%), "
                f"speed={chunk_count / total_emb if total_emb > 0 else 0:.1f} chunks/s\n"
                f"  - DB save: {total_db:.2f}s "
                f"({total_db / t_total * 100:.1f}%)\n"
                f"  - Qdrant write: {total_qdrant:.2f}s "
                f"({total_qdrant / t_total * 100:.1f}%), "
                f"speed={chunk_count / total_qdrant if total_qdrant > 0 else 0:.1f} points/s"
            )
        # ==========================================

        logger.info(
            f"{type(processor).__name__} streaming complete: {chunk_count} chunks "
            f"in {batch_index + 1} batches. "
            f"Memory adjustments: {mem_guard.adjustments}"
        )
        return chunk_count

    # ---- 普通文件：标准页级流式（回退） ----
    chunker = get_chunker()

    async for page in parse_file_pages(file_path, file_type):
        await task_svc.check_cancelled(task_id)

        page_text = page.text
        if not page_text.strip():
            pages_processed += 1
            continue

        # 第一页的 metadata 可以拿出来用
        if page.page_num == 0 and page.metadata:
            logger.info(f"Document metadata: {page.metadata}")

        # 对这一页分块（流式）
        for chunk_item in chunker.chunk_stream(page_text):
            # 补上页码和全局 index
            chunk_item.page_num = page.page_num
            # 注意：页级流式的 char_start/end 是页内偏移，不是全局的
            chunk_item.char_start = chunk_item.char_start  # 保持页内值
            chunk_item.char_end = chunk_item.char_end
            # 全局 index（防止唯一键冲突）
            chunk_item.index = global_chunk_index
            global_chunk_index += 1

            batch_chunks.append(chunk_item)

            # 攒够一批就处理
            if len(batch_chunks) >= mem_guard.current_batch_size:
                chunk_count += await _process_one_batch(
                    db=db,
                    qdrant=qdrant,
                    batch_chunks=batch_chunks,
                    embedding=embedding,
                    version=version,
                    document_id=document_id,
                    collection_id=collection_id,
                    directory_id=doc.directory_id or "",
                    file_name=doc.name,
                    file_type=file_type,
                    created_at=created_at,
                    batch_index=batch_index,
                )

                mem_guard.check_and_adjust()
                batch_chunks.clear()
                batch_index += 1
                await db.commit()

        pages_processed += 1

        # 更新进度
        if total_pages > 0:
            progress = prog_start + (pages_processed / total_pages) * (prog_end - prog_start) * 0.8
            # 加上嵌入和写入的进度修正
            progress = min(prog_end, progress)
            await task_svc.update_progress(
                task_id,
                progress,
                {
                    "stage": "processing_pages",
                    "page": pages_processed,
                    "total_pages": total_pages,
                    "chunks": chunk_count,
                    "batch_size": mem_guard.current_batch_size,
                    "memory_mb": round(get_memory_usage_mb(), 1),
                },
            )

    # 处理最后一批
    if batch_chunks:
        chunk_count += await _process_one_batch(
            db=db,
            qdrant=qdrant,
            batch_chunks=batch_chunks,
            embedding=embedding,
            version=version,
            document_id=document_id,
            collection_id=collection_id,
            directory_id=doc.directory_id or "",
            file_name=doc.name,
            file_type=file_type,
            created_at=created_at,
            batch_index=batch_index,
        )
        await db.commit()

    logger.info(
        f"Page streaming complete: {chunk_count} chunks from {pages_processed} pages "
        f"in {batch_index + 1} batches. "
        f"Memory adjustments: {mem_guard.adjustments}"
    )
    return chunk_count


# ------------------------------------------------------------------
# 单批处理（被 chunk 流式和页级流式共用）
# ------------------------------------------------------------------
async def _process_one_batch(
    db: AsyncSession,
    qdrant,
    batch_chunks: list,
    embedding,
    version: DocumentVersionDB,
    document_id: str,
    collection_id: str,
    directory_id: str,
    file_name: str,
    file_type: str,
    created_at: str,
    batch_index: int = 0,
    return_timings: bool = False,
) -> int | dict:
    """处理一批 chunk：向量化 → 写 DB → 写 Qdrant

    Args:
        batch_index: 批次序号，用于日志标识
        return_timings: 是否返回详细时间信息（dict），否则返回 int

    Returns:
        默认返回处理数量 (int)
        return_timings=True 时返回 dict:
            {"count": int, "embedding_time": float, "db_time": float, "qdrant_time": float}
    """
    batch_size = len(batch_chunks)
    if batch_size == 0:
        if return_timings:
            return {"count": 0, "embedding_time": 0.0, "db_time": 0.0, "qdrant_time": 0.0}
        return 0

    t0 = time.perf_counter()

    # 1. 向量化
    t_emb_start = time.perf_counter()
    texts = [c.text for c in batch_chunks]
    avg_text_len = sum(len(t) for t in texts) / len(texts) if texts else 0
    vectors = await embedding.embed_texts(texts)
    t_emb = time.perf_counter() - t_emb_start

    # 2. 写 DB
    t_db_start = time.perf_counter()
    chunk_objs = []
    for i, (item, vec) in enumerate(zip(batch_chunks, vectors)):
        chunk = ChunkDB(
            version_id=version.id,
            document_id=document_id,
            chunk_index=item.index if item.index is not None else i,
            text=item.text,
            content_hash=sha256_text(item.text),
            page_num=item.page_num,
            char_start=item.char_start,
            char_end=item.char_end,
            token_count=count_tokens(item.text),
        )
        db.add(chunk)
        chunk_objs.append(chunk)

    await db.flush()
    chunk_ids = [c.id for c in chunk_objs]
    t_db = time.perf_counter() - t_db_start

    # 3. 写 Qdrant
    t_qdrant_start = time.perf_counter()
    points = _build_qdrant_points(
        chunk_items=batch_chunks,
        chunk_ids=chunk_ids,
        vectors=vectors,
        document_id=document_id,
        version_id=version.id,
        collection_id=collection_id,
        directory_id=directory_id,
        file_name=file_name,
        file_type=file_type,
        created_at=created_at,
    )

    await qdrant.upsert_points(collection_id, points, batch_size=_get_qdrant_batch_size())
    t_qdrant = time.perf_counter() - t_qdrant_start

    total = time.perf_counter() - t0
    logger.debug(
        f"[Batch#{batch_index}] {batch_size} chunks done in {total:.2f}s | "
        f"embedding: {t_emb:.2f}s ({t_emb / total * 100:.0f}%), "
        f"db: {t_db:.2f}s ({t_db / total * 100:.0f}%), "
        f"qdrant: {t_qdrant:.2f}s ({t_qdrant / total * 100:.0f}%) | "
        f"avg_text_len: {avg_text_len:.0f} chars, "
        f"speed: {batch_size / total:.1f} chunks/s"
    )

    if return_timings:
        return {
            "count": batch_size,
            "embedding_time": t_emb,
            "db_time": t_db,
            "qdrant_time": t_qdrant,
            "total_time": total,
            "avg_text_len": avg_text_len,
        }
    return batch_size


# ------------------------------------------------------------------
# 辅助函数
# ------------------------------------------------------------------
def _build_qdrant_points(
    chunk_items: list,
    chunk_ids: list,
    vectors: list,
    document_id: str,
    version_id: str,
    collection_id: str,
    directory_id: str,
    file_name: str,
    file_type: str,
    created_at: str,
) -> list:
    """构建 Qdrant Point 列表"""
    points = []
    for i, chunk in enumerate(chunk_items):
        chunk_id = chunk_ids[i]
        payload = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "version_id": version_id,
            "collection_id": collection_id,
            "directory_id": directory_id,
            "chunk_index": chunk.index if chunk.index is not None else i,
            "content_hash": sha256_text(chunk.text),
            "text": chunk.text,
            "page_num": chunk.page_num if chunk.page_num is not None else -1,
            "file_name": file_name,
            "file_type": file_type,
            "created_at": created_at,
            "parent_text": chunk.parent_text or "",
            "parent_index": chunk.parent_index if chunk.parent_index is not None else -1,
        }
        points.append(
            {
                "id": chunk_id,
                "vector": vectors[i],
                "payload": payload,
            }
        )
    return points


async def _finalize_document(
    db: AsyncSession,
    qdrant,
    version: DocumentVersionDB,
    document_id: str,
    collection_id: str,
    chunk_count: int,
    task_id: str,
    task_svc: TaskService,
) -> None:
    """后处理：更新版本状态、切换 current_version、删除旧向量、更新计数"""
    await task_svc.check_cancelled(task_id)

    version.status = "completed"
    version.chunk_count = chunk_count

    await db.flush()

    doc_result = await db.execute(select(DocumentDB).where(DocumentDB.id == document_id))
    doc = doc_result.scalar_one()

    old_version_id = doc.current_version_id

    doc.current_version_id = version.id
    doc.status = "completed"
    doc.error_message = None

    await db.commit()

    if old_version_id and old_version_id != version.id:
        try:
            await qdrant.delete_by_version(collection_id, old_version_id)
            logger.info(f"Deleted old version vectors: {old_version_id}")
        except Exception as e:
            logger.warning(f"Failed to delete old version vectors: {e}")

    from app.models.db.collection import CollectionDB

    coll_result = await db.execute(select(CollectionDB).where(CollectionDB.id == collection_id))
    coll = coll_result.scalar_one_or_none()
    if coll:
        coll.chunk_count = coll.chunk_count + chunk_count
        if not old_version_id:
            coll.document_count = coll.document_count + 1
        await db.commit()


# ------------------------------------------------------------------
# 维度一致性保障：确保 Qdrant 集合维度与当前 embedding 模型匹配
# ------------------------------------------------------------------
async def _ensure_collection_dimension(
    db: AsyncSession,
    qdrant,
    collection_id: str,
    version: DocumentVersionDB,
) -> None:
    """
    检查 Qdrant 集合的向量维度是否与当前 embedding 模型一致。
    如果不一致，删除旧集合并用正确维度重建。

    触发场景：切换 embedding 模型后，旧 Collection 仍是旧维度，
    写入新向量时会报 dimension error。
    """
    from app.models.db.collection import CollectionDB
    from app.providers.embedding.factory import get_embedding

    # 获取当前 embedding 模型的维度
    embedding = await get_embedding()
    expected_dim = embedding.dimension
    if not expected_dim:
        logger.warning("Embedding dimension unknown, skipping dimension check")
        return

    # 获取 Qdrant 中实际的维度
    actual_dim = await qdrant.get_collection_vector_size(collection_id)
    if actual_dim is None:
        # 集合不存在，后面创建时会用正确维度，不用处理
        return

    if actual_dim == expected_dim:
        # 维度一致，没问题
        return

    # 维度不一致！需要重建
    logger.warning(
        f"Qdrant collection dimension mismatch: "
        f"expected={expected_dim} (current model), "
        f"actual={actual_dim} (existing collection). "
        f"Will recreate collection with correct dimension."
    )

    # 从 DB 读取 collection 配置（距离度量等）
    coll_result = await db.execute(select(CollectionDB).where(CollectionDB.id == collection_id))
    coll = coll_result.scalar_one_or_none()
    distance = coll.distance_metric if coll else "cosine"

    # 重建集合（删除旧的，用新维度创建）
    await qdrant.recreate_collection(
        collection_id=collection_id,
        vector_size=expected_dim,
        distance=distance,
    )

    # 更新 DB 中的维度记录
    if coll:
        coll.vector_dim = expected_dim
        coll.embedding_model = getattr(embedding, "model_name", "") or coll.embedding_model
        await db.commit()

    # 重置该版本的 chunk 计数（后续重新写入）
    version.chunk_count = 0

    logger.info(
        f"Collection '{collection_id}' recreated with dimension {expected_dim}. "
        f"Old vectors are cleared, will be rebuilt during processing."
    )


# ------------------------------------------------------------------
# 任务取消清理：删除已入库的部分数据
# ------------------------------------------------------------------
async def _cleanup_cancelled_task(
    db: AsyncSession,
    qdrant,
    collection_id: str,
    version_id: str,
    document_id: str,
    task_id: str,
) -> None:
    """
    任务取消时，清理已入库的部分数据：
    1. Qdrant 中该版本的向量
    2. chunks 表中该版本的记录
    3. document_records 表中该版本的记录（Excel Record 模式）

    注意：此函数要尽可能健壮，任何一步失败都要继续尝试清理其他部分。
    """
    logger.info(
        f"[Cleanup] Task {task_id} cancelled, cleaning up partial data for "
        f"version={version_id}, document={document_id}"
    )

    # 1. 清理 Qdrant 向量
    try:
        deleted = await qdrant.delete_by_version(collection_id, version_id)
        logger.info(f"[Cleanup] Deleted {deleted} Qdrant points for version {version_id}")
    except Exception as e:
        logger.error(f"[Cleanup] Failed to delete Qdrant points: {e}")

    # 2. 清理 chunks 表
    try:
        from sqlalchemy import delete

        result = await db.execute(delete(ChunkDB).where(ChunkDB.version_id == version_id))
        chunk_deleted = result.rowcount or 0
        await db.commit()
        logger.info(f"[Cleanup] Deleted {chunk_deleted} chunks from DB")
    except Exception as e:
        logger.error(f"[Cleanup] Failed to delete chunks from DB: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    # 3. 清理 document_records 表（Record 模式）
    try:
        from sqlalchemy import delete as sql_delete

        from app.models.db.document_record import DocumentRecordDB

        result = await db.execute(
            sql_delete(DocumentRecordDB).where(DocumentRecordDB.version_id == version_id)
        )
        record_deleted = result.rowcount or 0
        await db.commit()
        logger.info(f"[Cleanup] Deleted {record_deleted} document records from DB")
    except ImportError:
        # 模型不存在，跳过
        logger.debug("[Cleanup] DocumentRecordDB model not found, skipping")
    except Exception as e:
        logger.error(f"[Cleanup] Failed to delete document records from DB: {e}")
        try:
            await db.rollback()
        except Exception:
            pass

    logger.info(f"[Cleanup] Cleanup complete for task {task_id}")
