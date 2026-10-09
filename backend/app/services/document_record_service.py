"""
文档记录服务（Excel Record 模式的编排层）

负责：
- 将 Excel 文件流式读取为 RecordItem
- 批量写入 SQLite（document_records 表）
- 为每条记录生成向量化文本
- 调用 VectorService 将记录写入 Qdrant（语义索引）

核心职责划分：
- SQLite = 主存储（完整结构化数据，精确查询）
- Qdrant = 语义索引（用于语义搜索定位相关记录）
"""

import asyncio
import uuid
from typing import AsyncGenerator, List, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.chunkers.base import RecordItem
from app.core.logging import get_logger
from app.models.db.document_record import DocumentRecordDB
from app.processors.excel_processor import ExcelProcessor
from app.repositories.record_repo import RecordRepository

logger = get_logger(__name__)


class DocumentRecordService:
    """
    文档记录服务

    编排 Excel 流式读取 → SQLite 写入 → Qdrant 索引 的完整链路。
    """

    def __init__(self, db: AsyncSession, collection_id: str = ""):
        self.db = db
        self.collection_id = collection_id
        self.record_repo = RecordRepository(db)

    # ------------------------------------------------------------------
    # 导入流程
    # ------------------------------------------------------------------
    async def import_excel(
        self,
        file_path: str,
        document_id: str,
        version_id: str,
        batch_size: int = 500,
    ) -> int:
        """
        将 Excel 文件导入为结构化记录

        真正的流式导入：
        Excel → 逐行读取 → 分批写入 SQLite → 分批返回记录供 Qdrant 索引

        Args:
            file_path: Excel 文件路径
            document_id: 文档 ID
            version_id: 版本 ID
            batch_size: 每批写入数量

        Returns:
            导入的总记录数
        """
        processor = ExcelProcessor()
        total = 0
        batch: List[DocumentRecordDB] = []

        async for record_item in processor.record_stream(file_path, document_id):
            db_record = self._to_db_record(record_item, document_id, version_id)
            batch.append(db_record)

            if len(batch) >= batch_size:
                await self.record_repo.bulk_insert(batch, batch_size=batch_size)
                total += len(batch)
                batch = []
                logger.info(f"Import progress: {total} records")

        # 写入剩余
        if batch:
            await self.record_repo.bulk_insert(batch, batch_size=batch_size)
            total += len(batch)

        logger.info(f"Excel import complete: {total} records for doc {document_id}")
        return total

    async def import_excel_with_vectors(
        self,
        file_path: str,
        document_id: str,
        version_id: str,
        vector_service,
        qdrant_collection: str,
        batch_size: int = 500,
    ) -> dict:
        """
        导入 Excel 并同时建立 Qdrant 语义索引

        流式处理：逐批读取 → 写入 SQLite → 向量化 → 写入 Qdrant

        Args:
            file_path: Excel 文件路径
            document_id: 文档 ID
            version_id: 版本 ID
            vector_service: 向量服务实例
            qdrant_collection: Qdrant 集合名
            batch_size: 每批大小

        Returns:
            {"total_records": int, "total_vectors": int}
        """
        processor = ExcelProcessor()
        total_records = 0
        total_vectors = 0

        batch_records: List[DocumentRecordDB] = []
        batch_texts: List[str] = []
        batch_payloads: List[dict] = []

        async for record_item in processor.record_stream(file_path, document_id):
            db_record = self._to_db_record(record_item, document_id, version_id)
            batch_records.append(db_record)
            batch_texts.append(record_item.text)
            batch_payloads.append(self._make_qdrant_payload(record_item, db_record))

            if len(batch_records) >= batch_size:
                # 写入 SQLite
                await self.record_repo.bulk_insert(batch_records, batch_size=batch_size)
                total_records += len(batch_records)

                # 写入 Qdrant
                if vector_service and batch_texts:
                    try:
                        await self._upsert_vectors(
                            vector_service=vector_service,
                            collection_name=qdrant_collection,
                            records=batch_records,
                            texts=batch_texts,
                            payloads=batch_payloads,
                        )
                        total_vectors += len(batch_records)
                    except Exception as e:
                        logger.error(f"Failed to upsert vectors batch: {e}")

                # 更新 qdrant_point_id 到 DB
                for rec in batch_records:
                    if rec.qdrant_point_id:
                        # 已经在 _upsert_vectors 里设置了
                        pass

                batch_records = []
                batch_texts = []
                batch_payloads = []
                logger.info(f"Import progress: {total_records} records, {total_vectors} vectors")

        # 处理最后一批
        if batch_records:
            await self.record_repo.bulk_insert(batch_records, batch_size=batch_size)
            total_records += len(batch_records)

            if vector_service and batch_texts:
                try:
                    await self._upsert_vectors(
                        vector_service=vector_service,
                        collection_name=qdrant_collection,
                        records=batch_records,
                        texts=batch_texts,
                        payloads=batch_payloads,
                    )
                    total_vectors += len(batch_records)
                except Exception as e:
                    logger.error(f"Failed to upsert final vectors batch: {e}")

        logger.info(
            f"Excel import complete: {total_records} records, {total_vectors} vectors"
        )
        return {"total_records": total_records, "total_vectors": total_vectors}

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    async def count_distinct(
        self,
        document_id: str,
        column: str,
        filters: dict | None = None,
    ) -> int:
        """
        统计某列去重值数量（结构化查询的核心能力）

        直接走 SQLite，精确且快速。
        """
        return await self.record_repo.count_distinct(document_id, column, filters)

    async def query_records(
        self,
        document_id: str,
        filters: dict | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DocumentRecordDB]:
        """按条件查询记录列表"""
        return await self.record_repo.query_records(document_id, filters, limit, offset)

    async def get_record(self, record_id: str) -> Optional[DocumentRecordDB]:
        """按 ID 获取单条记录"""
        return await self.record_repo.get_by_id(record_id)

    async def list_sheets(self, document_id: str) -> List[str]:
        """获取文档中的 sheet 列表"""
        return await self.record_repo.list_sheets(document_id)

    async def list_distinct_values(
        self, document_id: str, column: str, limit: int = 200
    ) -> List[str]:
        """获取某列的去重值列表"""
        return await self.record_repo.list_distinct_values(document_id, column, limit)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _to_db_record(
        self,
        item: RecordItem,
        document_id: str,
        version_id: str,
    ) -> DocumentRecordDB:
        """RecordItem 转换为 ORM 模型"""
        rec = DocumentRecordDB(
            id=str(uuid.uuid4()),
            document_id=document_id,
            version_id=version_id,
            collection_id=self.collection_id,
            sheet_name=item.sheet_name,
            sheet_index=item.metadata.get("sheet_index", 0),
            row_number=item.row_number,
            record_type="data",
            vector_text=item.text,
        )
        # 设置 data（会自动序列化 JSON 并提取索引字段）
        rec.data = item.data
        return rec

    def _make_qdrant_payload(
        self, item: RecordItem, db_record: DocumentRecordDB
    ) -> dict:
        """
        构建 Qdrant payload

        只放必要的检索和展示字段，不放完整 data（太大）
        """
        payload = {
            "record_id": db_record.id,
            "document_id": db_record.document_id,
            "version_id": db_record.version_id,
            "collection_id": db_record.collection_id,
            "sheet_name": item.sheet_name,
            "row_number": item.row_number,
            "record_type": "data",
            # 常用业务字段（用于 Qdrant 端过滤）
            "sales_director": db_record.sales_director,
            "region_manager": db_record.region_manager,
            "region": db_record.region,
            "marketing_company": db_record.marketing_company,
            "salesperson": db_record.salesperson,
            "customer": db_record.customer,
            # 完整文本（方便快速查看）
            "text": item.text[:500],  # 截断，避免 payload 太大
        }
        return payload

    async def _upsert_vectors(
        self,
        vector_service,
        collection_name: str,
        records: List[DocumentRecordDB],
        texts: List[str],
        payloads: List[dict],
    ):
        """批量向量化并写入 Qdrant"""
        # 生成 embedding
        embeddings = await vector_service.encode_texts(texts)

        # 构建 Qdrant 点
        from qdrant_client.models import PointStruct

        points = []
        for i, (rec, embedding, payload) in enumerate(zip(records, embeddings, payloads)):
            point_id = str(rec.id)
            rec.qdrant_point_id = point_id
            payload["qdrant_point_id"] = point_id
            points.append(
                PointStruct(id=point_id, vector=embedding, payload=payload)
            )

        # 写入 Qdrant
        await vector_service.qdrant_repo.upsert_points(collection_name, points)
