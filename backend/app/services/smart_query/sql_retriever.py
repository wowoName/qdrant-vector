"""
SQL 检索器（结构化精确查询）

优先使用 PostgreSQL 的 document_records 表（Record 模式导入的结构化数据），
回退到 DuckDB（旧架构，直接查询 Excel 文件）。

返回统一的 RetrievalHit 列表。
"""

import re
import time
from typing import List

from sqlalchemy import select, func, and_, text as sa_text

from .base_retriever import BaseRetriever
from .schema_provider import SchemaProvider
from .types import (
    FilterInfo,
    QueryOperation,
    QueryPlan,
    RetrievalHit,
    RetrievalSource,
    TableSchema,
)
from app.core.logging import get_logger
from app.core.sql_compat import json_number, json_text, safe_rollback

logger = get_logger(__name__)


class SQLRetriever(BaseRetriever):
    """
    SQL 检索器

    结构化查询的主力，支持：
    - COUNT / COUNT_DISTINCT 统计
    - LIST / DETAIL 列表查询
    - SUM / AVG 聚合
    - TOP_N 排名
    - GROUP 分组统计
    """

    def __init__(self, schema_provider: SchemaProvider, db_session=None):
        self.schema_provider = schema_provider
        self.db = db_session  # SQLAlchemy async session（可选，有则优先查 document_records）

    # 列名安全校验正则（允许中文、英文字母、数字、下划线、全角/半角括号）
    # 最终安全保障是"列名必须在 schema 白名单中"，正则只是二次防护
    _COL_NAME_SAFE_RE = re.compile(r'^[\w\u4e00-\u9fa5（）()]+$')

    def is_applicable(self, plan: QueryPlan) -> bool:
        """结构化查询且有目标列时适用"""
        return plan.needs_sql and plan.operation != QueryOperation.UNKNOWN

    async def retrieve(
        self,
        query: str,
        plan: QueryPlan,
        schema: TableSchema,
        top_k: int = 20,
    ) -> List[RetrievalHit]:
        """执行 SQL 检索"""
        start = time.time()
        hits: List[RetrievalHit] = []

        try:
            # 优先走 PostgreSQL document_records 表
            if self.db and schema.document_id:
                hits = await self._retrieve_from_records(plan, schema, top_k)
            else:
                # 回退到 DuckDB
                hits = await self._retrieve_from_duckdb(query, plan, schema, top_k)

        except Exception as e:
            logger.error(f"SQL retrieval failed: {e}")
            # 回滚失败事务，避免污染共用 session 导致其它检索路径级联失败
            await safe_rollback(self.db)
            # 返回空结果，由 orchestrator 处理失败

        elapsed_ms = (time.time() - start) * 1000
        for i, hit in enumerate(hits):
            hit.rank = i + 1
            hit.metadata["execution_time_ms"] = elapsed_ms

        return hits

    # ------------------------------------------------------------------
    # document_records 路径（优先，更快更稳定）
    # ------------------------------------------------------------------
    async def _retrieve_from_records(
        self, plan: QueryPlan, schema: TableSchema, top_k: int
    ) -> List[RetrievalHit]:
        """从 PostgreSQL 的 document_records 表查询"""
        from app.models.db.document_record import DocumentRecordDB

        hits: List[RetrievalHit] = []
        document_id = schema.document_id

        # 常用列名映射（schema 列名 → DB 索引列）
        col_map = {
            "销售总监": "sales_director",
            "总监": "sales_director",
            "特区经理": "region_manager",
            "大区经理": "region_manager",
            "大区": "region",
            "销售公司": "region",
            "营销公司": "marketing_company",
            "特区": "marketing_company",
            "业务员": "salesperson",
            "销售员": "salesperson",
            "客商": "customer",
            "客户": "customer",
            "日期": "record_date",
            "销售总监 ": "sales_director",
            "特区经理 ": "region_manager",
            "大区 ": "region",
            "营销公司 ": "marketing_company",
            "业务员 ": "salesperson",
        }

        def _to_db_col(col_name: str) -> str:
            """schema 列名转换为 DB 列名，返回空字符串表示走 JSON 提取"""
            return col_map.get(col_name.strip(), "")

        # 构建 WHERE 条件（索引列走 ORM，其他走 JSON）
        conditions = [DocumentRecordDB.document_id == document_id]
        json_filters = {}

        for f in plan.filters:
            db_col = _to_db_col(f.column)
            if db_col:
                col_attr = getattr(DocumentRecordDB, db_col, None)
                if col_attr is not None:
                    conditions.append(col_attr == f.value)
            else:
                json_filters[f.column] = f.value

        target_db_col = _to_db_col(plan.target_column) if plan.target_column else ""

        # 根据操作类型查询
        op = plan.operation

        if op in (QueryOperation.COUNT,):
            hits = await self._count_records(
                plan, target_db_col, conditions, json_filters, schema
            )

        elif op in (QueryOperation.LIST, QueryOperation.DETAIL):
            hits = await self._list_records(
                plan, target_db_col, conditions, json_filters, schema, top_k
            )

        elif op == QueryOperation.TOP_N:
            hits = await self._top_n_records(
                plan, target_db_col, conditions, json_filters, schema, top_k
            )

        elif op in (QueryOperation.SUM, QueryOperation.AVG):
            hits = await self._aggregate_records(
                plan, target_db_col, conditions, json_filters, schema
            )

        elif op == QueryOperation.GROUP_COUNT:
            hits = await self._group_count_records(
                plan, conditions, json_filters, schema, top_k
            )

        else:
            # 未知操作，走列表查询兜底
            hits = await self._list_records(
                plan, target_db_col, conditions, json_filters, schema, top_k
            )

        return hits

    async def _count_records(
        self,
        plan: QueryPlan,
        target_db_col: str,
        conditions: list,
        json_filters: dict,
        schema: TableSchema,
    ) -> List[RetrievalHit]:
        """COUNT 查询"""
        from app.models.db.document_record import DocumentRecordDB
        import re

        count_val = 0
        schema_col_names = {c.name.strip() for c in schema.columns}

        # ---- 先构建 JSON 过滤条件（白名单校验防注入） ----
        json_where_parts = []
        json_params = {}
        for col, val in json_filters.items():
            col_clean = col.strip()
            if col_clean in schema_col_names and self._COL_NAME_SAFE_RE.match(col_clean):
                json_where_parts.append(f"{json_text('data_json', col_clean)} = :j_{col_clean}")
                json_params[f"j_{col_clean}"] = val

        # ---- 根据目标列位置选择查询方式 ----
        if target_db_col:
            # 目标列是索引列，走 ORM（索引列条件已在 conditions 中）
            col_attr = getattr(DocumentRecordDB, target_db_col)
            all_conditions = list(conditions)
            # JSON 过滤条件用 sa_text 拼入
            if json_where_parts:
                for jw in json_where_parts:
                    all_conditions.append(sa_text(jw))
            stmt = select(func.count(func.distinct(col_attr))).where(and_(*all_conditions))
            result = await self.db.execute(stmt, json_params)
            count_val = result.scalar() or 0

        elif plan.target_column and plan.target_column.strip() in schema_col_names:
            # 目标列在 JSON 中，走原生 SQL
            target_clean = plan.target_column.strip()
            if self._COL_NAME_SAFE_RE.match(target_clean):
                all_where = ["document_id = :doc_id"] + json_where_parts
                params = {"doc_id": schema.document_id, **json_params}
                # 索引列条件（如 sales_director = 'xxx'）也需要加入 WHERE
                # 从 conditions 中提取（conditions 是 ORM 构建的条件列表）
                # 简化处理：直接用 ORM + JSON 取值表达式组合
                all_conditions = list(conditions)
                for jw in json_where_parts:
                    all_conditions.append(sa_text(jw))
                stmt = (
                    select(func.count(func.distinct(sa_text(json_text('data_json', target_clean)))))
                    .where(and_(*all_conditions))
                )
                result = await self.db.execute(stmt, json_params)
                count_val = result.scalar() or 0

        else:
            # 无明确目标列，count 行数
            all_conditions = list(conditions)
            for jw in json_where_parts:
                all_conditions.append(sa_text(jw))
            stmt = select(func.count(DocumentRecordDB.id)).where(and_(*all_conditions))
            result = await self.db.execute(stmt, json_params)
            count_val = result.scalar() or 0

        # ---- 构造返回 ----
        display_col = plan.target_column_alias or plan.target_column or "数量"
        hit = RetrievalHit(
            source=RetrievalSource.SQL,
            score=1.0,
            content=f"{display_col}：{count_val}",
            payload={
                "type": "aggregate",
                "operation": "count_distinct",
                "target_column": plan.target_column,
                "target_column_alias": plan.target_column_alias,
                "value": count_val,
                "filters": [(f.column, f.value) for f in plan.filters],
                "total_count": count_val,  # 总数（与 value 一致，便于合成层识别）
            },
            metadata={"query": "count_distinct", "result_count": 1},
        )

        # ---- 附加：COUNT 查询同时返回明细列表（更符合用户预期）----
        # 用户问"有多少个 X"时，通常也想看看具体是哪些 X
        list_hits: List[RetrievalHit] = []
        try:
            # 最多返回 50 条明细，足够展示
            list_limit = min(50, max(count_val, 0))
            if list_limit > 0:
                list_hits = await self._list_records(
                    plan, target_db_col, conditions, json_filters, schema, list_limit
                )
                # 给明细 hit 打上标记，表明是 COUNT 查询附带的明细
                for h in list_hits:
                    h.payload = h.payload or {}
                    h.payload["from_count_query"] = True
        except Exception as e:
            logger.warning(f"Failed to get detail list for count query: {e}")
            await safe_rollback(self.db)

        # 第一个是聚合结果，后面是明细
        return [hit] + list_hits

    async def _list_records(
        self,
        plan: QueryPlan,
        target_db_col: str,
        conditions: list,
        json_filters: dict,
        schema: TableSchema,
        top_k: int,
    ) -> List[RetrievalHit]:
        """列表查询（返回总数 + 前 top_k 条结果）

        SQL 是精确查询，不应该被 top_k 截断数据完整性。
        做法：先查总数，再查前 top_k 条，总数信息放在第一个 hit 的 payload 中。

        两种模式：
        1. 有明确目标列 → DISTINCT 去重列表（如"有哪些销售经理"）
        2. 无明确目标列 → 完整行列表（如"销售记录列表"）
        """
        from app.models.db.document_record import DocumentRecordDB

        hits: List[RetrievalHit] = []
        schema_col_names = {c.name.strip() for c in schema.columns}

        # ---- 构建所有 WHERE 条件（索引列 + JSON 字段） ----
        all_conditions = list(conditions)
        json_params = {}

        for col, val in json_filters.items():
            col_clean = col.strip()
            if col_clean in schema_col_names and self._COL_NAME_SAFE_RE.match(col_clean):
                all_conditions.append(
                    sa_text(f"{json_text('data_json', col_clean)} = :j_{col_clean}")
                )
                json_params[f"j_{col_clean}"] = val

        # ---- 判断是否需要去重（有明确目标列时做 DISTINCT） ----
        target_col = plan.target_column or ""
        target_col_clean = target_col.strip() if target_col else ""
        has_distinct_target = (
            target_col_clean
            and target_col_clean in schema_col_names
            and self._COL_NAME_SAFE_RE.match(target_col_clean)
        )

        if has_distinct_target:
            # ---- 模式1：DISTINCT 去重列表（如"有哪些销售经理"） ----
            hits = await self._distinct_list_records(
                plan, target_db_col, target_col_clean,
                all_conditions, json_params, schema, top_k
            )
        else:
            # ---- 模式2：完整行列表（如"销售记录列表"） ----
            hits = await self._full_row_list_records(
                plan, all_conditions, json_params, top_k
            )

        return hits

    async def _distinct_list_records(
        self, plan, target_db_col, target_col_clean,
        all_conditions, json_params, schema, top_k
    ) -> List[RetrievalHit]:
        """DISTINCT 去重列表查询（有明确目标列时使用）

        例如"有哪些销售经理"、"负责X客户的销售经理都有谁"
        返回去重后的目标列值列表 + 去重后的总数
        """
        from app.models.db.document_record import DocumentRecordDB

        hits: List[RetrievalHit] = []
        values: list = []

        if target_db_col:
            # 目标列是索引列
            col_attr = getattr(DocumentRecordDB, target_db_col, None)
            if col_attr is not None:
                # 去重总数
                count_stmt = select(func.count(func.distinct(col_attr))).where(and_(*all_conditions))
                total_count = (await self.db.execute(count_stmt, json_params)).scalar() or 0
                # 去重后的值列表（全量返回，不做 LIMIT）
                stmt = (
                    select(func.distinct(col_attr))
                    .where(and_(*all_conditions))
                    .order_by(col_attr)
                )
                result = await self.db.execute(stmt, json_params)
                values = [row[0] for row in result.fetchall() if row[0] is not None]
        else:
            # 目标列在 JSON 中
            json_col_expr = sa_text(json_text('data_json', target_col_clean))
            # 去重总数
            count_stmt = select(func.count(func.distinct(json_col_expr))).where(and_(*all_conditions))
            total_count = (await self.db.execute(count_stmt, json_params)).scalar() or 0
            # 去重后的值列表（全量返回，不做 LIMIT）
            stmt = (
                select(func.distinct(json_col_expr))
                .where(and_(*all_conditions))
                .order_by(json_col_expr)
            )
            result = await self.db.execute(stmt, json_params)
            values = [row[0] for row in result.fetchall() if row[0] is not None]

        # 构造 hits
        display_col = plan.target_column_alias or target_col_clean
        for i, val in enumerate(values):
            hit = RetrievalHit(
                source=RetrievalSource.SQL,
                score=1.0,
                content=str(val),
                payload={
                    "type": "distinct_list",
                    "column": target_col_clean,
                    "column_alias": display_col,
                    "value": val,
                },
            )
            # 第一条附带总数信息
            if i == 0:
                hit.payload["_total_count"] = total_count
                hit.payload["_shown_count"] = len(values)
                hit.metadata["total_count"] = total_count
                hit.metadata["shown_count"] = len(values)
            hits.append(hit)

        # 空结果也要返回总数
        if not hits:
            empty_hit = RetrievalHit(
                source=RetrievalSource.SQL,
                score=0.0,
                content="",
                payload={
                    "type": "distinct_list",
                    "column": target_col_clean,
                    "_total_count": 0,
                    "_shown_count": 0,
                    "empty": True,
                },
                metadata={"total_count": 0, "shown_count": 0},
            )
            hits.append(empty_hit)

        return hits

    async def _full_row_list_records(
        self, plan, all_conditions, json_params, top_k
    ) -> List[RetrievalHit]:
        """完整行列表查询（无明确目标列时使用）

        例如"销售记录列表"，返回完整的记录行
        """
        from app.models.db.document_record import DocumentRecordDB

        hits: List[RetrievalHit] = []

        # ---- Step 1: 先查总数 ----
        total_count = 0
        try:
            count_stmt = select(func.count(DocumentRecordDB.id)).where(and_(*all_conditions))
            count_result = await self.db.execute(count_stmt, json_params)
            total_count = count_result.scalar() or 0
        except Exception as e:
            logger.warning(f"Failed to count total records for list query: {e}")
            await safe_rollback(self.db)

        # ---- Step 2: 全量查询明细行（不做 LIMIT） ----
        stmt = (
            select(DocumentRecordDB)
            .where(and_(*all_conditions))
            .order_by(DocumentRecordDB.row_number)
        )
        result = await self.db.execute(stmt, json_params)
        records = list(result.scalars().all())

        for i, rec in enumerate(records):
            hit = RetrievalHit(
                source=RetrievalSource.SQL,
                score=1.0,
                record_id=rec.id,
                document_id=rec.document_id,
                sheet_name=rec.sheet_name,
                row_number=rec.row_number,
                content=rec.vector_text or "",
                payload=rec.data,
            )
            # 第一条记录附带总数信息（合成器会读取）
            if i == 0:
                hit.payload = hit.payload or {}
                hit.payload["_total_count"] = total_count
                hit.payload["_shown_count"] = len(records)
                hit.metadata["total_count"] = total_count
                hit.metadata["shown_count"] = len(records)
            hits.append(hit)

        # 如果没有任何记录，也返回一个空的"摘要 hit"携带总数信息
        if not hits:
            empty_hit = RetrievalHit(
                source=RetrievalSource.SQL,
                score=0.0,
                content="",
                payload={
                    "_total_count": total_count,
                    "_shown_count": 0,
                    "empty": True,
                },
                metadata={"total_count": total_count, "shown_count": 0},
            )
            hits.append(empty_hit)

        return hits

    async def _top_n_records(
        self, plan, target_db_col, conditions, json_filters, schema, top_k
    ) -> List[RetrievalHit]:
        """Top N 排名查询

        TOP_N 本身就是要"前 N 名"，所以保留 LIMIT。
        N 从 plan.limit 中取（用户说的前五/前十等），而不是 top_k 参数。
        """
        from app.models.db.document_record import DocumentRecordDB

        schema_col_names = {c.name.strip() for c in schema.columns}

        # 构建 JSON 过滤条件
        json_where_parts = []
        json_params = {}
        for col, val in json_filters.items():
            col_clean = col.strip()
            if col_clean in schema_col_names and self._COL_NAME_SAFE_RE.match(col_clean):
                json_where_parts.append(f"{json_text('data_json', col_clean)} = :j_{col_clean}")
                json_params[f"j_{col_clean}"] = val

        all_conditions = list(conditions)
        for jw in json_where_parts:
            all_conditions.append(sa_text(jw))

        # 确定 N：plan.limit 优先，否则用 top_k
        n = plan.limit if plan.limit and plan.limit > 0 else top_k
        n = max(1, min(n, 1000))  # 安全范围

        target_col = plan.target_column or ""
        target_col_clean = target_col.strip() if target_col else ""

        # 有明确目标列时：按目标列排序取前 N
        if target_col_clean and target_col_clean in schema_col_names and self._COL_NAME_SAFE_RE.match(target_col_clean):
            target_db_col_clean = target_db_col
            if target_db_col_clean:
                # 索引列排序
                col_attr = getattr(DocumentRecordDB, target_db_col_clean, None)
                if col_attr is not None:
                    # 先查总数
                    count_stmt = select(func.count(DocumentRecordDB.id)).where(and_(*all_conditions))
                    total_count = (await self.db.execute(count_stmt, json_params)).scalar() or 0
                    # 再查前 N 条（倒序，默认数值从大到小）
                    stmt = (
                        select(DocumentRecordDB)
                        .where(and_(*all_conditions))
                        .order_by(col_attr.desc())
                        .limit(n)
                    )
                    result = await self.db.execute(stmt, json_params)
                    records = list(result.scalars().all())
                    return self._records_to_hits(records, total_count, "top_n")
            else:
                # JSON 列排序
                json_col_expr = sa_text(json_number('data_json', target_col_clean))
                # 总数
                count_stmt = select(func.count(DocumentRecordDB.id)).where(and_(*all_conditions))
                total_count = (await self.db.execute(count_stmt, json_params)).scalar() or 0
                # 前 N 条
                stmt = (
                    select(DocumentRecordDB)
                    .where(and_(*all_conditions))
                    .order_by(sa_text(f"{json_number('data_json', target_col_clean)} DESC"))
                    .limit(n)
                )
                result = await self.db.execute(stmt, json_params)
                records = list(result.scalars().all())
                return self._records_to_hits(records, total_count, "top_n")

        # 没有目标列：按行号取前 N
        count_stmt = select(func.count(DocumentRecordDB.id)).where(and_(*all_conditions))
        total_count = (await self.db.execute(count_stmt, json_params)).scalar() or 0
        stmt = (
            select(DocumentRecordDB)
            .where(and_(*all_conditions))
            .order_by(DocumentRecordDB.row_number)
            .limit(n)
        )
        result = await self.db.execute(stmt, json_params)
        records = list(result.scalars().all())
        return self._records_to_hits(records, total_count, "top_n")

    def _records_to_hits(self, records, total_count: int, hit_type: str) -> List[RetrievalHit]:
        """将 ORM 记录转为 RetrievalHit 列表（附带总数信息）"""
        hits: List[RetrievalHit] = []
        for i, rec in enumerate(records):
            hit = RetrievalHit(
                source=RetrievalSource.SQL,
                score=1.0,
                record_id=rec.id,
                document_id=rec.document_id,
                sheet_name=rec.sheet_name,
                row_number=rec.row_number,
                content=rec.vector_text or "",
                payload=dict(rec.data or {}),
            )
            if i == 0:
                hit.payload["_total_count"] = total_count
                hit.payload["_shown_count"] = len(records)
                hit.payload["type"] = hit_type
                hit.metadata["total_count"] = total_count
                hit.metadata["shown_count"] = len(records)
            hits.append(hit)
        return hits

    async def _aggregate_records(
        self, plan, target_db_col, conditions, json_filters, schema
    ) -> List[RetrievalHit]:
        """SUM / AVG 聚合查询（真正的数值聚合）"""
        from app.models.db.document_record import DocumentRecordDB

        schema_col_names = {c.name.strip() for c in schema.columns}
        op = plan.operation  # SUM / AVG

        # ---- 构建 JSON 过滤条件（白名单校验防注入）----
        json_where_parts = []
        json_params = {}
        for col, val in json_filters.items():
            col_clean = col.strip()
            if col_clean in schema_col_names and self._COL_NAME_SAFE_RE.match(col_clean):
                json_where_parts.append(f"{json_text('data_json', col_clean)} = :j_{col_clean}")
                json_params[f"j_{col_clean}"] = val

        # ---- 构建完整的 WHERE 条件 ----
        all_conditions = list(conditions)
        for jw in json_where_parts:
            all_conditions.append(sa_text(jw))

        # ---- 决定聚合目标列（数值列） ----
        target_col = plan.target_column or ""
        target_col_clean = target_col.strip() if target_col else ""
        aggregate_value = 0.0
        total_count = 0

        # 策略1：目标列是索引列（目前索引列都是文本，暂不支持数值索引，留作扩展）
        if target_db_col:
            col_attr = getattr(DocumentRecordDB, target_db_col, None)
            if col_attr is not None and op == QueryOperation.SUM:
                stmt = select(func.sum(col_attr)).where(and_(*all_conditions))
                result = await self.db.execute(stmt, json_params)
                aggregate_value = float(result.scalar() or 0)
                total_count_stmt = select(func.count()).where(and_(*all_conditions))
                total_count = (await self.db.execute(total_count_stmt, json_params)).scalar() or 0
            elif col_attr is not None and op == QueryOperation.AVG:
                stmt = select(func.avg(col_attr)).where(and_(*all_conditions))
                result = await self.db.execute(stmt, json_params)
                aggregate_value = float(result.scalar() or 0)
                total_count_stmt = select(func.count()).where(and_(*all_conditions))
                total_count = (await self.db.execute(total_count_stmt, json_params)).scalar() or 0

        # 策略2：目标列在 JSON 中（常见情况：销售金额等数值列）
        elif target_col_clean and target_col_clean in schema_col_names \
                and self._COL_NAME_SAFE_RE.match(target_col_clean):
            # 使用方言化的 JSON 取值表达式提取数值字段做聚合
            json_col_expr = sa_text(json_number('data_json', target_col_clean))
            if op == QueryOperation.SUM:
                stmt = select(func.sum(json_col_expr)).where(and_(*all_conditions))
                result = await self.db.execute(stmt, json_params)
                aggregate_value = float(result.scalar() or 0)
            elif op == QueryOperation.AVG:
                stmt = select(func.avg(json_col_expr)).where(and_(*all_conditions))
                result = await self.db.execute(stmt, json_params)
                aggregate_value = float(result.scalar() or 0)

            # 同时统计记录数
            count_stmt = select(func.count()).where(and_(*all_conditions))
            total_count = (await self.db.execute(count_stmt, json_params)).scalar() or 0

        # 策略3：没有明确目标列，返回 0（兜底）
        else:
            aggregate_value = 0.0
            total_count = 0

        # 格式化显示值（保留2位小数，去除尾部无意义的零）
        if isinstance(aggregate_value, float):
            # 如果是整数，显示为整数；否则保留2位小数
            if aggregate_value == int(aggregate_value):
                display_value = int(aggregate_value)
            else:
                display_value = round(aggregate_value, 2)
        else:
            display_value = aggregate_value

        display_col = plan.target_column_alias or plan.target_column or "结果"
        op_label = "合计" if op == QueryOperation.SUM else "平均值"

        hit = RetrievalHit(
            source=RetrievalSource.SQL,
            score=1.0,
            content=f"{display_col}{op_label}：{display_value}",
            payload={
                "type": "aggregate",
                "operation": op.value,
                "target_column": plan.target_column,
                "target_column_alias": plan.target_column_alias,
                "value": display_value,
                "filters": [(f.column, f.value) for f in plan.filters],
                "total_count": total_count,  # 参与聚合的记录总数
                "unit": "元" if "金额" in (plan.target_column or "") or "销售额" in (plan.target_column or "") else "",
            },
            metadata={"query": op.value, "result_count": 1, "total_records": total_count},
        )

        # ---- 附加：聚合查询同时返回部分明细列表（方便用户验证）----
        list_hits: List[RetrievalHit] = []
        try:
            # 最多返回 10 条明细（聚合类查询不需要太多明细）
            list_limit = min(10, max(total_count, 0))
            if list_limit > 0:
                list_hits = await self._list_records(
                    plan, target_db_col, conditions, json_filters, schema, list_limit
                )
                for h in list_hits:
                    h.payload = h.payload or {}
                    h.payload["from_aggregate_query"] = True
        except Exception as e:
            logger.warning(f"Failed to get detail list for aggregate query: {e}")
            await safe_rollback(self.db)

        # 第一个是聚合结果，后面是明细
        return [hit] + list_hits

    async def _group_count_records(
        self, plan, conditions, json_filters, schema, top_k
    ) -> List[RetrievalHit]:
        """分组统计（简化：走列表查询）"""
        return await self._list_records(
            plan, plan.group_by, conditions, json_filters, schema, top_k
        )

    # ------------------------------------------------------------------
    # DuckDB 回退路径（旧架构兼容）
    # ------------------------------------------------------------------
    async def _retrieve_from_duckdb(
        self, query: str, plan: QueryPlan, schema: TableSchema, top_k: int
    ) -> List[RetrievalHit]:
        """回退到 DuckDB 直接查询 Excel"""
        from .sql_agent import SQLAgent

        sql_agent = SQLAgent(self.schema_provider)
        sql_result = await sql_agent.execute(
            query, plan, schema,
            file_path=schema.file_path,
            sheet_name=schema.sheet_name or "",
        )

        hits: List[RetrievalHit] = []
        if not sql_result.success:
            return hits

        # 聚合查询（count/sum/avg 等）返回 1 条聚合结果
        if plan.operation in (QueryOperation.COUNT, QueryOperation.SUM, QueryOperation.AVG):
            val = sql_result.get_single_value()
            display_col = plan.target_column_alias or plan.target_column or "结果"
            hit = RetrievalHit(
                source=RetrievalSource.SQL,
                score=1.0,
                content=f"{display_col}：{val}",
                payload={
                    "type": "aggregate",
                    "operation": plan.operation.value,
                    "value": val,
                    "sql": sql_result.sql,
                },
            )
            hits.append(hit)

        # 列表/详情查询返回多行（全量返回，不做 top_k 截断）
        else:
            for i, row in enumerate(sql_result.rows):
                content_parts = []
                for j, col in enumerate(sql_result.columns):
                    if j < len(row):
                        content_parts.append(f"{col}：{row[j]}")
                hit = RetrievalHit(
                    source=RetrievalSource.SQL,
                    score=1.0,
                    content="；".join(content_parts),
                    payload={
                        "row": row,
                        "columns": sql_result.columns,
                        "sql": sql_result.sql,
                    },
                    sheet_name=schema.sheet_name,
                )
                hits.append(hit)

        return hits
