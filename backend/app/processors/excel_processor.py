"""
Excel 文档处理器
专属处理 .xlsx / .xls 文件，支持两种模式：

1. **Record 模式（默认）** — 业务型 Excel
   - 一行 = 一条结构化记录
   - 真正的流式读取（read_only 模式，逐行 yield）
   - SQLite 存完整记录，Qdrant 存语义索引

2. **Chunk 模式（兼容旧流程）** — 文档型 Excel
   - 使用 TabularChunker，支持 row / row_group / column_group / sql
   - 适合长文本表格 / 非结构化表格

优势：
- Record 模式：语义单元是"行"，查询时结构化 + 语义双路
- Chunk 模式：保留原有能力，处理长文本表格
- 流式读取：大文件内存占用低（逐行迭代，不加载全表）
- Forward Fill：处理合并单元格空值继承
"""

import os
import time
from typing import AsyncGenerator, Iterator, List

from app.chunkers.base import ChunkItem, RecordItem
from app.chunkers.tabular_chunker import TabularChunker
from app.core.config import get_tabular_config, settings
from app.core.logging import get_logger
from app.parsers.base import ParseResult
from app.parsers.excel_parser import ExcelParser
from app.utils.text_cleaner import normalize_text

from .base import BaseDocumentProcessor, ProcessInfo

logger = get_logger(__name__)


class ExcelProcessor(BaseDocumentProcessor):
    """
    Excel 专属处理器

    支持 Record 模式和 Chunk 模式两种处理方式：
    - Record 模式（默认）：业务表格，一行一条记录
    - Chunk 模式：文档型表格，按行/行组/列组分块
    """

    def __init__(self, file_type: str | None = None):
        self._parser = ExcelParser()
        self._chunker: TabularChunker | None = None
        self._file_type = file_type or "xlsx"
        self._config_cache: dict | None = None

    def supported_types(self) -> list[str]:
        return ["xlsx", "xls", "excel"]

    # ------------------------------------------------------------------
    # 解析接口（小文件 / 兼容旧流程）
    # ------------------------------------------------------------------
    async def parse(self, file_path: str) -> ParseResult:
        """全量解析 Excel（小文件兼容模式）"""
        t0 = time.perf_counter()
        result = await self._parser.parse(file_path)
        elapsed = time.perf_counter() - t0
        tables_count = len(result.tables) if result.tables else 0
        total_rows = sum(len(t) for t in result.tables) if result.tables else 0
        logger.info(
            f"[ExcelParser] Parsed {file_path}: {elapsed:.2f}s, "
            f"sheets={tables_count}, total_rows={total_rows}"
        )
        return result

    # ------------------------------------------------------------------
    # 切片接口（Chunk 模式 / 文档型 Excel）
    # ------------------------------------------------------------------
    async def chunk(self, file_path: str) -> list[ChunkItem]:
        """全量切片（Chunk 模式）"""
        t0 = time.perf_counter()

        chunker = self._get_chunker()
        mode = self._get_mode()

        # record 模式下，chunk() 返回 Record 转换的 ChunkItem（兼容旧接口）
        if mode == "record":
            records = await self.records(file_path)
            chunks = [self._record_to_chunk(r, i) for i, r in enumerate(records)]
            elapsed = time.perf_counter() - t0
            logger.info(
                f"[ExcelChunker] {file_path}: {elapsed:.2f}s, "
                f"mode=record, chunks={len(chunks)}"
            )
            return chunks

        parse_result = await self.parse(file_path)

        if not parse_result.tables or not any(len(t) >= 2 for t in parse_result.tables):
            # 没有有效表格数据，退化为文本切片
            from app.chunkers.recursive_chunker import RecursiveChunker

            fallback = RecursiveChunker()
            if parse_result.pages:
                chunks = fallback.chunk_with_pages(parse_result.pages)
            else:
                chunks = fallback.chunk(parse_result.text)
            elapsed = time.perf_counter() - t0
            logger.info(
                f"[ExcelChunker] {file_path}: {elapsed:.2f}s, "
                f"mode=fallback_text, chunks={len(chunks)}"
            )
            return chunks

        sheet_names = parse_result.metadata.get("sheets") if parse_result.metadata else None
        chunks = chunker.chunk_tables(
            tables=parse_result.tables,
            sheet_names=sheet_names,
            file_path=file_path,
            file_type=self._detect_type(file_path),
        )
        elapsed = time.perf_counter() - t0
        logger.info(
            f"[ExcelChunker] {file_path}: {elapsed:.2f}s, "
            f"mode=tabular/{chunker.mode}, chunks={len(chunks)}"
        )
        return chunks

    async def chunk_stream(self, file_path: str) -> AsyncGenerator[ChunkItem, None]:
        """流式切片"""
        mode = self._get_mode()

        # record 模式下，流式生成 ChunkItem
        if mode == "record":
            idx = 0
            async for record in self.record_stream(file_path):
                yield self._record_to_chunk(record, idx)
                idx += 1
            return

        chunker = self._get_chunker()
        parse_result = await self.parse(file_path)

        if not parse_result.tables or not any(len(t) >= 2 for t in parse_result.tables):
            from app.chunkers.recursive_chunker import RecursiveChunker

            fallback = RecursiveChunker()
            if parse_result.pages:
                for c in fallback.chunk_stream_with_pages(parse_result.pages):
                    yield c
            else:
                for c in fallback.chunk_stream(parse_result.text):
                    yield c
            return

        sheet_names = parse_result.metadata.get("sheets") if parse_result.metadata else None
        for chunk in chunker.chunk_tables_stream(
            tables=parse_result.tables,
            sheet_names=sheet_names,
            file_path=file_path,
            file_type=self._detect_type(file_path),
        ):
            yield chunk

    # ------------------------------------------------------------------
    # Record 接口（⭐ 业务型 Excel 主流程）
    # ------------------------------------------------------------------
    async def records(self, file_path: str, document_id: str = "") -> list[RecordItem]:
        """
        全量读取为 RecordItem 列表

        Args:
            file_path: Excel 文件路径
            document_id: 文档 ID，用于生成 record_id

        Returns:
            RecordItem 列表
        """
        return [r async for r in self.record_stream(file_path, document_id)]

    async def record_stream(
        self,
        file_path: str,
        document_id: str = "",
    ) -> AsyncGenerator[RecordItem, None]:
        """
        ⭐ 流式读取为 RecordItem（真正的大文件流式）

        使用 openpyxl read_only 模式逐行迭代，
        不将整个 Excel 加载到内存，支持 GB 级大文件。

        Args:
            file_path: Excel 文件路径
            document_id: 文档 ID，用于生成 record_id

        Yields:
            RecordItem，一行一个
        """
        import asyncio

        t_start = time.perf_counter()
        ext = self._detect_type(file_path)
        file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
        logger.info(
            f"[Excel-Perf] Record stream start: {file_path}, "
            f"ext={ext}, size={file_size/1024/1024:.2f}MB"
        )
        record_count = 0

        if ext in ("xlsx", "xls", "excel"):
            # 在后台线程中运行同步生成器，通过 asyncio 队列桥接
            async for record in self._sync_gen_to_async(
                self._record_stream_xlsx_sync, file_path, document_id
            ):
                yield record
                record_count += 1
        else:
            # 其他类型暂时走全量（xls 需要 xlrd，不支持流式）
            import functools
            records = await asyncio.to_thread(
                functools.partial(self._records_sync_fallback, file_path, document_id)
            )
            for r in records:
                yield r
                record_count += 1

        t_total = time.perf_counter() - t_start
        speed = record_count / t_total if t_total > 0 else 0
        logger.info(
            f"[Excel-Perf] Record stream done: {file_path}, "
            f"records={record_count}, time={t_total:.2f}s, speed={speed:.1f} records/s"
        )

    async def _sync_gen_to_async(
        self, sync_gen_func, *args, batch_size: int = 100, **kwargs
    ) -> AsyncGenerator[RecordItem, None]:
        """
        将同步生成器函数包装为异步生成器（在独立线程中运行）

        使用 Queue + 批量读取，减少 async 线程切换开销。
        """
        import asyncio
        import threading
        from queue import Queue, Empty

        q: "Queue" = Queue(maxsize=200)
        sentinel = object()
        error_holder: list = []

        def producer():
            try:
                for item in sync_gen_func(*args, **kwargs):
                    q.put(item)
            except Exception as e:
                error_holder.append(e)
            finally:
                q.put(sentinel)

        thread = threading.Thread(target=producer, daemon=True)
        thread.start()

        loop = asyncio.get_event_loop()

        try:
            while True:
                # 先阻塞取第一个
                first = await loop.run_in_executor(None, q.get)
                if first is sentinel:
                    break
                if isinstance(first, Exception):
                    raise first

                # 非阻塞取更多（最多 batch_size-1 个）
                batch = [first]
                done = False
                try:
                    while len(batch) < batch_size:
                        item = q.get_nowait()
                        if item is sentinel:
                            done = True
                            break
                        if isinstance(item, Exception):
                            raise item
                        batch.append(item)
                except Empty:
                    pass

                # 批量 yield
                for item in batch:
                    yield item

                if done:
                    break
        finally:
            thread.join(timeout=5)

        # 检查生产者线程是否有错误
        if error_holder:
            raise error_holder[0]

    def _record_stream_xlsx_sync(
        self, file_path: str, document_id: str
    ) -> Iterator[RecordItem]:
        """
        xlsx 流式读取（同步生成器，基于 openpyxl read_only + iter_rows）

        这是真正的流式实现：
        - read_only=True 不加载整个文件到内存
        - iter_rows 逐行迭代
        - 边读边 yield，内存占用稳定

        在独立线程中被调用，通过 _sync_gen_to_async 桥接为异步。
        """
        from openpyxl import load_workbook
        from typing import Iterator

        t_open_start = time.perf_counter()
        wb = load_workbook(file_path, read_only=True, data_only=True)
        t_open = time.perf_counter() - t_open_start
        logger.debug(f"[Excel-Perf] Workbook opened in {t_open:.3f}s")

        total_records = 0
        try:
            sheet_names = wb.sheetnames
            logger.debug(f"[Excel-Perf] Sheets: {len(sheet_names)} - {sheet_names[:5]}...")

            for sheet_idx, sheet_name in enumerate(sheet_names):
                t_sheet_start = time.perf_counter()
                ws = wb[sheet_name]

                # Forward Fill 缓冲区（逐列维护，处理合并单元格空值）
                last_values: list = []

                row_num = 0  # 数据行号（不含表头）
                headers: list = []
                empty_rows_skipped = 0

                for row in ws.iter_rows(values_only=True):
                    # 跳过全空行
                    if all(v is None or str(v).strip() == "" for v in row):
                        empty_rows_skipped += 1
                        continue

                    row_strs = ["" if v is None else str(v).strip() for v in row]

                    if not headers:
                        # 第一行有效行作为表头
                        headers = row_strs
                        # 初始化 forward fill 缓冲区
                        last_values = [None] * len(headers)
                        continue

                    # Forward Fill（按列填补空值，解决合并单元格问题）
                    filled = list(row_strs)
                    for i in range(len(filled)):
                        if i < len(last_values):
                            if not filled[i] and last_values[i] is not None:
                                filled[i] = last_values[i]
                            elif filled[i]:
                                last_values[i] = filled[i]

                    # 跳过全空行（forward fill 后）
                    if all(v == "" or v is None for v in filled):
                        empty_rows_skipped += 1
                        continue

                    # 构建 data dict
                    data = {}
                    for i, val in enumerate(filled):
                        if i < len(headers):
                            data[headers[i]] = val

                    # 构建向量化文本
                    text_parts = []
                    for i, val in enumerate(filled):
                        if i < len(headers) and val:
                            text_parts.append(f"{headers[i]}：{val}")
                    text = "；".join(text_parts)

                    record_id = self._make_record_id(document_id, sheet_idx, row_num, sheet_name)

                    yield RecordItem(
                        record_id=record_id,
                        sheet_name=sheet_name,
                        row_number=row_num,
                        data=data,
                        text=text,
                        metadata={
                            "sheet_index": sheet_idx,
                            "document_id": document_id,
                        },
                    )
                    row_num += 1
                    total_records += 1

                t_sheet = time.perf_counter() - t_sheet_start
                logger.debug(
                    f"[Excel-Perf] Sheet '{sheet_name}' done: {row_num} records, "
                    f"time={t_sheet:.3f}s, skipped_empty={empty_rows_skipped}, "
                    f"speed={row_num/t_sheet if t_sheet > 0 else 0:.1f} records/s"
                )

        finally:
            wb.close()

        logger.debug(
            f"[Excel-Perf] All sheets done: total_records={total_records}"
        )

    def _records_sync_fallback(self, file_path: str, document_id: str) -> list[RecordItem]:
        """
        全量回退：非 xlsx 文件或流式失败时使用
        先全量解析，再转换为 RecordItem
        """
        import asyncio

        parse_result = asyncio.get_event_loop().run_until_complete(self.parse(file_path))
        records = []
        sheet_names = parse_result.metadata.get("sheets", []) if parse_result.metadata else []

        for sheet_idx, table in enumerate(parse_result.tables):
            if not table or len(table) < 2:
                continue
            headers = table[0]
            sheet_name = sheet_names[sheet_idx] if sheet_idx < len(sheet_names) else f"Sheet{sheet_idx + 1}"

            for row_num, row in enumerate(table[1:]):
                data = {}
                text_parts = []
                for i, val in enumerate(row):
                    if i < len(headers) and val:
                        data[headers[i]] = val
                        text_parts.append(f"{headers[i]}：{val}")

                record_id = self._make_record_id(document_id, sheet_idx, row_num, sheet_name)
                records.append(RecordItem(
                    record_id=record_id,
                    sheet_name=sheet_name,
                    row_number=row_num,
                    data=data,
                    text="；".join(text_parts),
                    metadata={
                        "sheet_index": sheet_idx,
                        "document_id": document_id,
                    },
                ))

        return records

    # ------------------------------------------------------------------
    # 信息获取
    # ------------------------------------------------------------------
    def get_info(self, file_path: str) -> ProcessInfo:
        """快速获取 Excel 文件信息（sheet 数、估算行数/记录数）"""
        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".xlsx":
                return self._get_info_xlsx(file_path)
            elif ext == ".xls":
                return self._get_info_xls(file_path)
        except Exception as e:
            logger.debug(f"Failed to get Excel info: {e}")

        return ProcessInfo(total_pages=0, estimated_chunks=0)

    def _get_info_xlsx(self, file_path: str) -> ProcessInfo:
        """
        快速获取 xlsx 信息（只读打开）

        注意：max_row 在 read_only 模式下对某些文件不准确，
        这里做一个抽样验证：如果 max_row 异常大（>10万），
        实际扫描前面若干行估算真实数据行数。
        """
        from openpyxl import load_workbook

        wb = load_workbook(file_path, read_only=True, data_only=True)
        try:
            total_rows = 0
            sheet_names = wb.sheetnames
            sheet_count = len(sheet_names)

            for sheet_name in sheet_names:
                ws = wb[sheet_name]
                max_row = ws.max_row or 0

                # 异常值修正：如果 max_row 非常大（可能是编辑残留），
                # 用抽样方式估算真实数据行数
                if max_row > 100000:
                    actual = self._estimate_real_rows(ws, max_row)
                    total_rows += max(0, actual - 1)  # 减表头
                else:
                    total_rows += max(0, max_row - 1)  # 减表头
        finally:
            wb.close()

        cfg = self._get_chunk_config()
        mode = cfg.get("mode", "record")

        if mode == "record":
            est_chunks = total_rows  # 一行一个 record
        elif mode == "row":
            est_chunks = total_rows
        elif mode == "row_group":
            rows_per = cfg.get("rows_per_chunk") or 10
            est_chunks = max(1, (total_rows + rows_per - 1) // rows_per)  # ceil
        elif mode == "column_group":
            est_chunks = sheet_count * 3
        else:
            est_chunks = total_rows

        return ProcessInfo(
            total_pages=sheet_count,
            estimated_chunks=est_chunks,
            metadata={"sheets": sheet_names, "total_data_rows": total_rows, "mode": mode},
        )

    def _estimate_real_rows(self, ws, claimed_max: int, sample_size: int = 1000) -> int:
        """
        当 max_row 异常大时，抽样估算真实数据行数

        策略：
        1. 先读前 sample_size 行，统计非空行比例
        2. 如果前 sample_size 行非空比例 > 80%，认为数据量确实大
        3. 否则从后往前找最后一个非空行
        """
        non_empty = 0
        total_scanned = 0
        last_non_empty_row = 0

        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i >= sample_size:
                break
            total_scanned += 1
            if any(v is not None and str(v).strip() != "" for v in row):
                non_empty += 1
                last_non_empty_row = i

        if total_scanned == 0:
            return 0

        # 如果非空比例 > 80%，认为数据确实多
        if non_empty / total_scanned > 0.8:
            return claimed_max

        # 否则从后往前找（read_only 模式不支持反向，只能顺序读完）
        # 简化策略：直接返回已扫描的最后非空行
        return last_non_empty_row + 1

    def _get_info_xls(self, file_path: str) -> ProcessInfo:
        """获取 xls 文件信息"""
        try:
            import xlrd

            wb = xlrd.open_workbook(file_path)
            total_rows = 0
            for i in range(wb.nsheets):
                ws = wb.sheet_by_index(i)
                total_rows += max(0, ws.nrows - 1)
            sheet_names = wb.sheet_names()

            cfg = self._get_chunk_config()
            mode = cfg.get("mode", "record")

            if mode == "record" or mode == "row":
                est_chunks = total_rows
            elif mode == "row_group":
                rows_per = cfg.get("rows_per_chunk") or 10
                est_chunks = max(1, (total_rows + rows_per - 1) // rows_per)
            else:
                est_chunks = total_rows

            return ProcessInfo(
                total_pages=wb.nsheets,
                estimated_chunks=est_chunks,
                metadata={"sheets": sheet_names, "total_data_rows": total_rows, "mode": mode},
            )
        except ImportError:
            return ProcessInfo()

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _get_mode(self) -> str:
        """获取当前处理模式（record / row / row_group / column_group / sql）"""
        cfg = self._get_chunk_config()
        return cfg.get("mode", "record")

    def _get_chunk_config(self) -> dict:
        """获取该处理器的切片配置（带缓存）"""
        if self._config_cache is None:
            self._config_cache = get_tabular_config(self._file_type)
        return self._config_cache

    def _get_chunker(self) -> TabularChunker:
        """获取或创建切片器实例（使用 per-type 配置）"""
        if self._chunker is None:
            cfg = self._get_chunk_config()
            self._chunker = TabularChunker(
                mode=cfg.get("mode", "row"),
                rows_per_chunk=cfg.get("rows_per_chunk"),
                columns_per_group=cfg.get("columns_per_group", 10),
                include_headers=cfg.get("include_headers", True),
                chunk_size=cfg.get("size", settings.chunk_size),
                chunk_overlap=cfg.get("overlap", settings.chunk_overlap),
            )
        return self._chunker

    def _detect_type(self, file_path: str) -> str:
        """从文件路径检测文件类型"""
        ext = os.path.splitext(file_path)[1].lower().lstrip(".")
        return ext if ext else "xlsx"

    def _make_record_id(self, document_id: str, sheet_idx: int, row_num: int, sheet_name: str = "") -> str:
        """生成记录唯一 ID"""
        doc_part = document_id or "unknown"
        return f"{doc_part}:{sheet_idx}:{row_num}"

    def _record_to_chunk(self, record: RecordItem, index: int) -> ChunkItem:
        """RecordItem 转换为 ChunkItem（兼容旧接口）"""
        return ChunkItem(
            text=record.text,
            index=index,
            char_start=0,
            char_end=len(record.text),
            page_num=record.metadata.get("sheet_index", 0),
        )

    def close(self):
        if self._chunker:
            self._chunker.close()
            self._chunker = None
