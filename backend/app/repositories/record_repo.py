"""
文档记录仓储（PostgreSQL 结构化记录）

负责 Excel Record 模式下的结构化记录存储与查询。

核心职责：
- 批量写入记录（支持流式分批）
- 结构化查询（COUNT / DISTINCT / WHERE / GROUP BY）
- 按记录 ID 获取完整数据

设计理念：
- PostgreSQL = 查（精确结构化查询）
- Qdrant = 找（语义搜索定位）
"""

from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from sqlalchemy import delete, func, select, text, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.sql_compat import json_text
from app.models.db.document_record import DocumentRecordDB

logger = get_logger(__name__)


class RecordRepository:
    """文档记录仓储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ------------------------------------------------------------------
    # 写入
    # ------------------------------------------------------------------
    async def add_record(self, record: DocumentRecordDB) -> str:
        """新增单条记录，返回记录 ID"""
        self.db.add(record)
        await self.db.flush()
        return record.id

    async def bulk_insert(
        self,
        records: List[DocumentRecordDB],
        batch_size: int = 500,
    ) -> int:
        """
        批量插入记录

        Args:
            records: 记录列表
            batch_size: 每批大小

        Returns:
            插入总数
        """
        if not records:
            return 0

        total = 0
        for i in range(0, len(records), batch_size):
            batch = records[i:i + batch_size]
            self.db.add_all(batch)
            await self.db.flush()
            total += len(batch)
            logger.debug(f"Bulk insert batch: {len(batch)} records, total: {total}")

        return total

    async def delete_by_document(self, document_id: str) -> int:
        """删除某文档的所有记录"""
        result = await self.db.execute(
            delete(DocumentRecordDB).where(
                DocumentRecordDB.document_id == document_id
            )
        )
        return result.rowcount or 0

    async def delete_by_version(self, version_id: str) -> int:
        """删除某版本的所有记录"""
        result = await self.db.execute(
            delete(DocumentRecordDB).where(
                DocumentRecordDB.version_id == version_id
            )
        )
        return result.rowcount or 0

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    async def get_by_id(self, record_id: str) -> Optional[DocumentRecordDB]:
        """按 ID 获取单条记录"""
        result = await self.db.execute(
            select(DocumentRecordDB).where(DocumentRecordDB.id == record_id)
        )
        return result.scalar_one_or_none()

    async def get_by_qdrant_point(self, point_id: str) -> Optional[DocumentRecordDB]:
        """按 Qdrant 点 ID 获取记录"""
        result = await self.db.execute(
            select(DocumentRecordDB).where(
                DocumentRecordDB.qdrant_point_id == point_id
            )
        )
        return result.scalar_one_or_none()

    async def count_by_document(self, document_id: str) -> int:
        """统计某文档的记录总数"""
        result = await self.db.execute(
            select(func.count(DocumentRecordDB.id)).where(
                DocumentRecordDB.document_id == document_id
            )
        )
        return result.scalar() or 0

    async def count_distinct(
        self,
        document_id: str,
        column_name: str,
        filters: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        统计某列的去重值数量（结构化查询核心方法）

        Args:
            document_id: 文档 ID
            column_name: 列名（支持索引列名或 JSON 字段名）
            filters: 过滤条件 {列名: 值}

        Returns:
            去重计数
        """
        # 判断列是索引列还是 JSON 字段
        indexed_cols = {
            "sales_director": DocumentRecordDB.sales_director,
            "region_manager": DocumentRecordDB.region_manager,
            "region": DocumentRecordDB.region,
            "marketing_company": DocumentRecordDB.marketing_company,
            "salesperson": DocumentRecordDB.salesperson,
            "customer": DocumentRecordDB.customer,
            "sheet_name": DocumentRecordDB.sheet_name,
            "record_date": DocumentRecordDB.record_date,
        }

        # 构建 WHERE 条件
        conditions = [DocumentRecordDB.document_id == document_id]
        json_filters = {}  # 需要走 JSON 提取的过滤条件

        if filters:
            for col, val in filters.items():
                if col in indexed_cols:
                    conditions.append(indexed_cols[col] == val)
                else:
                    json_filters[col] = val

        # 如果目标列和所有过滤条件都是索引列，直接用 SQL 查询
        target_is_indexed = column_name in indexed_cols

        if target_is_indexed and not json_filters:
            # 纯索引列查询，高效
            target_col = indexed_cols[column_name]
            stmt = select(func.count(func.distinct(target_col))).where(and_(*conditions))
            result = await self.db.execute(stmt)
            return result.scalar() or 0

        # 如果涉及 JSON 字段，使用方言化的 JSON 取值表达式
        target_expr = (
            indexed_cols[column_name].name
            if target_is_indexed
            else json_text("data_json", column_name)
        )

        where_clauses = ["document_id = :doc_id"]
        params = {"doc_id": document_id}

        if filters:
            for col, val in filters.items():
                if col in indexed_cols:
                    where_clauses.append(f"{indexed_cols[col].name} = :filter_{col}")
                    params[f"filter_{col}"] = val
                else:
                    where_clauses.append(
                        f"{json_text('data_json', col)} = :filter_{col}"
                    )
                    params[f"filter_{col}"] = val

        sql = text(
            f"SELECT COUNT(DISTINCT {target_expr}) FROM document_records "
            f"WHERE {' AND '.join(where_clauses)}"
        )
        result = await self.db.execute(sql, params)
        row = result.fetchone()
        return row[0] if row else 0

    async def query_records(
        self,
        document_id: str,
        filters: Optional[Dict[str, Any]] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[DocumentRecordDB]:
        """
        按条件查询记录列表

        Args:
            document_id: 文档 ID
            filters: 过滤条件
            limit: 最大返回数
            offset: 偏移量

        Returns:
            记录列表
        """
        indexed_cols = {
            "sales_director": DocumentRecordDB.sales_director,
            "region_manager": DocumentRecordDB.region_manager,
            "region": DocumentRecordDB.region,
            "marketing_company": DocumentRecordDB.marketing_company,
            "salesperson": DocumentRecordDB.salesperson,
            "customer": DocumentRecordDB.customer,
            "sheet_name": DocumentRecordDB.sheet_name,
            "record_date": DocumentRecordDB.record_date,
        }

        conditions = [DocumentRecordDB.document_id == document_id]
        json_filters: Dict[str, Any] = {}

        if filters:
            for col, val in filters.items():
                if col in indexed_cols:
                    conditions.append(indexed_cols[col] == val)
                else:
                    json_filters[col] = val

        stmt = select(DocumentRecordDB).where(and_(*conditions))

        if json_filters:
            # 涉及 JSON 字段过滤，改用原生 SQL
            where_clauses = ["document_id = :doc_id"]
            params: Dict[str, Any] = {"doc_id": document_id}

            # 先加上索引列过滤
            for col, val in filters.items():
                if col in indexed_cols:
                    where_clauses.append(f"{indexed_cols[col].name} = :f_{col}")
                    params[f"f_{col}"] = val
                else:
                    where_clauses.append(
                        f"{json_text('data_json', col)} = :f_{col}"
                    )
                    params[f"f_{col}"] = val

            sql = text(
                f"SELECT * FROM document_records "
                f"WHERE {' AND '.join(where_clauses)} "
                f"LIMIT :limit OFFSET :offset"
            )
            params["limit"] = limit
            params["offset"] = offset

            result = await self.db.execute(sql, params)
            rows = result.fetchall()
            # 转换为 ORM 对象
            records = []
            for row in rows:
                rec = DocumentRecordDB(
                    id=row.id,
                    document_id=row.document_id,
                    version_id=row.version_id,
                    collection_id=row.collection_id,
                    sheet_name=row.sheet_name,
                    sheet_index=row.sheet_index,
                    row_number=row.row_number,
                    record_type=row.record_type,
                    data_json=row.data_json,
                    vector_text=row.vector_text,
                    qdrant_point_id=row.qdrant_point_id,
                    sales_director=row.sales_director,
                    region_manager=row.region_manager,
                    region=row.region,
                    marketing_company=row.marketing_company,
                    salesperson=row.salesperson,
                    customer=row.customer,
                    record_date=row.record_date,
                    created_at=row.created_at,
                )
                records.append(rec)
            return records

        stmt = stmt.order_by(DocumentRecordDB.row_number).limit(limit).offset(offset)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_sheets(self, document_id: str) -> List[str]:
        """获取文档中所有 sheet 名称"""
        result = await self.db.execute(
            select(func.distinct(DocumentRecordDB.sheet_name))
            .where(DocumentRecordDB.document_id == document_id)
            .order_by(DocumentRecordDB.sheet_name)
        )
        return [r for r in result.scalars().all()]

    async def list_distinct_values(
        self,
        document_id: str,
        column_name: str,
        limit: int = 200,
    ) -> List[str]:
        """获取某列的去重值列表"""
        indexed_cols = {
            "sales_director": DocumentRecordDB.sales_director,
            "region_manager": DocumentRecordDB.region_manager,
            "region": DocumentRecordDB.region,
            "marketing_company": DocumentRecordDB.marketing_company,
            "salesperson": DocumentRecordDB.salesperson,
            "customer": DocumentRecordDB.customer,
            "sheet_name": DocumentRecordDB.sheet_name,
            "record_date": DocumentRecordDB.record_date,
        }

        if column_name in indexed_cols:
            col = indexed_cols[column_name]
            stmt = (
                select(func.distinct(col))
                .where(
                    and_(
                        DocumentRecordDB.document_id == document_id,
                        col.isnot(None),
                        col != "",
                    )
                )
                .limit(limit)
            )
            result = await self.db.execute(stmt)
            return [r for r in result.scalars().all() if r]

        # JSON 字段（PostgreSQL 不允许在 WHERE 中引用 SELECT 别名，这里内联表达式）
        expr = json_text("data_json", column_name)
        sql = text(
            f"SELECT DISTINCT {expr} AS val "
            f"FROM document_records "
            f"WHERE document_id = :doc_id "
            f"AND {expr} IS NOT NULL AND {expr} != '' "
            f"LIMIT :limit"
        )
        result = await self.db.execute(sql, {"doc_id": document_id, "limit": limit})
        return [row[0] for row in result.fetchall() if row[0]]
