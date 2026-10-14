"""
SmartQueryOrchestrator - 智能问数编排器（多路并行架构）

整个 SmartQuery 系统的入口，负责协调各个检索器和合成器。

工作流程（多路并行）：
                    用户问题
                       │
                       ▼
              ┌─────────────────┐
              │   RouterAgent   │  意图分类 + 实体解析 + 查询计划
              └────────┬────────┘
                       │
           ┌───────────┼───────────┐
           ▼           ▼           ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │SQL       │ │Vector    │ │Keyword   │   三路并行检索
     │Retriever │ │Retriever │ │Retriever │
     └─────┬────┘ └─────┬────┘ └─────┬────┘
           │            │            │
           └────────────┼────────────┘
                        ▼
              ┌─────────────────┐
              │  ResultFusion   │   结果融合（去重+RRF排序）
              └────────┬────────┘
                       │
                       ▼
              ┌─────────────────┐
              │SynthesizerAgent │   答案生成（大模型综合）
              └────────┬────────┘
                       │
                       ▼
                   最终回答

设计原则：
- 多路并行：SQL / Vector / Keyword 三路同时检索
- 结果融合：RRF 算法合并多路结果，去重重排序
- 容错性：某一路失败不影响整体，自动降级
- 可观测性：完整记录每路的结果、耗时、状态
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.db.document import DocumentDB, DocumentVersionDB
from app.providers.storage.factory import get_storage
from app.services.vector_service import VectorService
from app.services.query_state import extract_profile_subject

from .constants import (
    BRIEF_SQL_HITS_LIMIT,
    CHINESE_KEYWORD_RE,
    DEFAULT_MAX_TABLES_PER_SUBQUERY,
    DEFAULT_TOP_K,
    ENTITY_TABLE_BOOSTS,
    QUERY_PREFIX_PATTERNS,
    QUERY_STOPWORDS,
    SQL_DETAIL_MAX_K_AGG,
    SQL_DETAIL_MAX_K_DEFAULT,
    TABLE_RANK_WEIGHTS,
)
from .keyword_retriever import KeywordRetriever
from .query_decomposer import QueryDecomposer
from .result_fusion import ResultFusion
from .router_agent import RouterAgent
from .schema_provider import SchemaProvider
from .sql_retriever import SQLRetriever
from .synthesizer_agent import SynthesizerAgent
from .types import (
    AgentContext,
    MultiRetrievalResult,
    QueryIntent,
    QueryOperation,
    QueryPlan,
    RetrievalQueryResult,
    SQLQueryResult,
    TableSchema,
)
from .vector_retriever import VectorRetriever

logger = get_logger(__name__)


class SmartQueryOrchestrator:
    """
    智能问数编排器（多路并行架构）

    协调 SQL / Vector / Keyword 三路检索器，
    结果融合后交给 SynthesizerAgent 生成最终答案。
    """

    def __init__(
        self,
        db: AsyncSession,
        collection_id: str = "",
        visible_ids: list | None = None,
        router_agent: RouterAgent | None = None,
        sql_retriever: SQLRetriever | None = None,
        vector_retriever: VectorRetriever | None = None,
        keyword_retriever: KeywordRetriever | None = None,
        synthesizer_agent: SynthesizerAgent | None = None,
        schema_provider: SchemaProvider | None = None,
        fusion_strategy: str = "rrf",
    ):
        self.db = db
        self.collection_id = collection_id
        # SaaS 可见范围（None 表示不过滤）
        self.visible_ids = visible_ids
        self.storage = get_storage()

        # 共享 SchemaProvider（避免重复加载数据）
        self.schema_provider = schema_provider or SchemaProvider()

        # Agent 层
        self.router_agent = router_agent or RouterAgent(self.schema_provider)
        self.synthesizer_agent = synthesizer_agent or SynthesizerAgent()
        self.query_decomposer = QueryDecomposer()

        # 检索器层（三路并行）
        self.sql_retriever = sql_retriever or SQLRetriever(self.schema_provider, db)
        self.keyword_retriever = keyword_retriever or KeywordRetriever(db)

        if vector_retriever:
            self.vector_retriever = vector_retriever
        else:
            vector_service = VectorService(db)
            self.vector_retriever = VectorRetriever(vector_service=vector_service, db=db, visible_ids=visible_ids)

        # 结果融合器
        self.fusion = ResultFusion(strategy=fusion_strategy)

        # 全局配置
        self.enable_smart_query = getattr(settings, "smart_query_enabled", True)
        self.sql_min_confidence = getattr(settings, "smart_query_sql_min_confidence", 0.4)
        self.max_sql_files = getattr(settings, "smart_query_max_sql_files", 3)
        self.enable_keyword_search = True  # 关键词检索开关
        self.default_top_k = DEFAULT_TOP_K

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def query(
        self,
        user_query: str,
        collection_id: str = "",
        session_id: str = "",
        history_messages: list[dict] | None = None,
        top_k: int = 10,
        inherited_state: dict | None = None,
    ) -> AgentContext:
        """
        执行智能问数查询（多路并行架构）

        Args:
            user_query: 用户问题
            collection_id: 知识库ID
            session_id: 会话ID
            history_messages: 历史消息
            top_k: 每路检索返回的 top_k
            inherited_state: 从会话状态继承的范围/维度（多轮继承）

        Returns:
            AgentContext 包含完整的执行结果
        """
        start_time = time.time()
        effective_collection = collection_id or self.collection_id

        # 入口统一预处理：清洗用户 query
        cleaned_query = self._preprocess_query(user_query)

        context = AgentContext(
            query=cleaned_query,
            collection_id=effective_collection,
            session_id=session_id,
            history_messages=history_messages or [],
        )

        # 记录预处理信息
        context.debug_info["original_query"] = user_query
        if cleaned_query != user_query:
            context.debug_info["cleaned_query"] = cleaned_query
            logger.debug(f"Query preprocessed: '{user_query}' -> '{cleaned_query}'")

        try:
            # Step 1: 查询分解（多意图问题拆分为多个子查询）
            decomposition = await self.query_decomposer.decompose(cleaned_query)
            context.debug_info["decomposition"] = {
                "method": decomposition.method,
                "is_multi_intent": decomposition.is_multi_intent,
                "sub_queries": [sq.query for sq in decomposition.sub_queries],
                "error": decomposition.error,
            }

            if decomposition.error:
                context.error = decomposition.error
                context.final_answer = decomposition.error
                context.total_time_ms = (time.time() - start_time) * 1000
                return context

            if decomposition.is_multi_intent and len(decomposition.sub_queries) > 1:
                # 多意图问题：子查询并行执行，结果合并后综合回答
                logger.info(
                    f"Multi-intent query detected ({decomposition.method}): "
                    f"{len(decomposition.sub_queries)} sub-queries"
                )
                await self._multi_query_path(
                    context=context,
                    decomposition=decomposition,
                    effective_collection=effective_collection,
                    top_k=top_k,
                    session_id=session_id,
                    history_messages=history_messages,
                    inherited_state=inherited_state,
                )
                context.total_time_ms = (time.time() - start_time) * 1000
                return context

            # 单意图：走正常的 SmartQuery 流程
            # Step 1: 查找表格文件（Excel/CSV）
            table_files = await self._find_table_files(effective_collection)

            if not table_files:
                # 没有表格文件，只走向量检索
                logger.info("No table files found, using vector search only")
                await self._vector_only_path(context, cleaned_query, effective_collection, top_k)
                context.total_time_ms = (time.time() - start_time) * 1000
                return context

            # 排序（所有表都排序，后面根据查询类型决定取多少个）
            if len(table_files) > 1:
                table_files = self._rank_table_files(table_files, cleaned_query)

            # Step 2: 加载所有表的结构（用于后面的选择和分析）
            all_table_schemas = []  # list of (file_info, schema)
            for f in table_files:
                schema = await self._load_schema(f)
                if schema:
                    all_table_schemas.append((f, schema))

            if not all_table_schemas:
                # 所有schema都加载失败，降级为纯向量检索
                await self._vector_only_path(context, cleaned_query, effective_collection, top_k)
                context.total_time_ms = (time.time() - start_time) * 1000
                return context

            # Step 2.5: 基于 schema 列名做二次排名（比纯文件名排名更精准）
            if len(all_table_schemas) > 1:
                files_only = [f for f, _ in all_table_schemas]
                schemas_only = [s for _, s in all_table_schemas]
                re_ranked_files = self._rank_table_files(files_only, cleaned_query, schemas_only)
                # 根据重新排名后的文件顺序，重新组织 all_table_schemas
                file_to_schema = {id(f): (f, s) for f, s in all_table_schemas}
                all_table_schemas = [
                    file_to_schema[id(f)] for f in re_ranked_files if id(f) in file_to_schema
                ]
                context.debug_info["reranked_files"] = [
                    f["file_name"] for f, _ in all_table_schemas
                ]

            # 主表（排名第一）
            primary_file, primary_schema = all_table_schemas[0]
            context.debug_info["target_file"] = primary_file["file_path"]
            context.debug_info["document_id"] = primary_file["document_id"]
            context.debug_info["schema_columns"] = len(primary_schema.columns)
            context.debug_info["schema_rows"] = primary_schema.row_count

            # Step 3: RouterAgent 分析查询意图（主表）
            plan = await self.router_agent.analyze(
                query=cleaned_query,
                file_path=primary_file["file_path"],
                sheet_name=primary_file.get("sheet_name", ""),
                schema=primary_schema,
                inherited_state=inherited_state,
            )
            context.query_plan = plan
            # 回传本次生效的过滤条件（含来源元数据，供上层持久化为会话范围）
            context.effective_filters = [
                {
                    "column": f.column,
                    "value": f.value,
                    "operator": f.operator,
                    "column_alias": f.column_alias,
                    "source": getattr(f, "source", "explicit"),
                    "turn": getattr(f, "turn", 0),
                    "explicit": getattr(f, "explicit", True),
                    "confidence": getattr(f, "confidence", 1.0),
                }
                for f in plan.filters
            ]
            # 维度槽位（供多轮继承分析对象/度量/条数）
            if plan.operation == QueryOperation.GROUP_AGG:
                context.effective_scope = {
                    "group_by": plan.group_by,
                    "metric": plan.metric_column,
                    "order_dir": plan.order_dir,
                    "limit": plan.limit,
                }

            logger.info(
                f"Query analysis: intent={plan.intent.value}, "
                f"op={plan.operation.value}, "
                f"confidence={plan.confidence:.2f}"
            )

            # Step 3.5: 根据查询类型决定参与 SQL 检索的表数量
            # - 语义类型驱动：根据 plan 涉及的语义类型，选择所有相关表 + 兜底表
            # - 语义类型无法判断时，退回到 _resolve_max_tables 的 top-N 策略
            # - 兜底表 15 张（宁可多查也不漏，准确性优先）
            table_schemas = self._select_semantically_relevant_tables(
                plan=plan,
                primary_schema=primary_schema,
                all_schemas=all_table_schemas,
                min_fallback=15,
            )
            context.debug_info["candidate_files"] = [f["file_name"] for f, _ in table_schemas]
            context.debug_info["max_tables"] = len(table_schemas)
            context.debug_info["total_tables"] = len(all_table_schemas)
            context.debug_info["selection_mode"] = "semantic_type"

            # Step 4: 多路并行检索（使用智能 top_k）
            # 优先级：plan.suggested_top_k > 传入的 top_k > 默认值
            actual_top_k = self._resolve_top_k(top_k, plan)
            context.debug_info["resolved_top_k"] = actual_top_k

            multi_result = await self._parallel_retrieval_multi_table(
                query=cleaned_query,
                plan=plan,
                table_schemas=table_schemas,
                collection_id=effective_collection,
                top_k=actual_top_k,
                inherited_state=inherited_state,
            )
            context.multi_retrieval = multi_result
            context.used_sql = bool(multi_result.sql_hits and multi_result.sql_success)
            context.used_vector = bool(multi_result.vector_hits and multi_result.vector_success)
            context.used_keyword = bool(multi_result.keyword_hits and multi_result.keyword_success)

            # 兼容旧字段（保留 SQLQueryResult 和 RetrievalQueryResult）
            if multi_result.sql_hits:
                context.sql_result = self._hits_to_sql_result(multi_result.sql_hits, plan)
            if multi_result.vector_hits:
                context.retrieval_result = self._hits_to_retrieval_result(multi_result.vector_hits)

            # Step 5: SynthesizerAgent 生成最终答案
            answer, references = await self.synthesizer_agent.synthesize_multi(
                query=cleaned_query,
                plan=plan,
                multi_result=multi_result,
                context=context,
            )

            context.final_answer = answer
            context.references = references
            context.is_kb_answer = multi_result.total_hits > 0

        except Exception as e:
            logger.error(f"SmartQuery orchestration failed: {e}", exc_info=True)
            context.error = str(e)
            context.final_answer = f"抱歉，处理查询时出错了：{str(e)}"
            context.is_kb_answer = False

        context.total_time_ms = (time.time() - start_time) * 1000

        # 记录性能指标
        sources = context.multi_retrieval.sources_used if context.multi_retrieval else []
        logger.info(
            f"SmartQuery completed: intent={context.query_plan.intent.value if context.query_plan else 'unknown'}, "
            f"sources={sources}, "
            f"total_hits={context.multi_retrieval.total_hits if context.multi_retrieval else 0}, "
            f"total_time={context.total_time_ms:.0f}ms"
        )

        return context

    # ------------------------------------------------------------------
    # 多路并行检索
    # ------------------------------------------------------------------

    async def _parallel_retrieval(
        self,
        query: str,
        plan: QueryPlan,
        schema: TableSchema,
        collection_id: str,
        top_k: int,
    ) -> MultiRetrievalResult:
        """
        三路并行检索 + 结果融合

        返回 MultiRetrievalResult，包含各路独立结果和融合结果。
        """
        result = MultiRetrievalResult()

        # 构建任务列表
        tasks = {}

        # SQL 检索（结构化查询时启用）
        use_sql = plan.needs_sql and plan.confidence >= self.sql_min_confidence
        if use_sql and self.sql_retriever.is_applicable(plan):
            tasks["sql"] = self._run_sql_retrieval(query, plan, schema, top_k)

        # 向量检索（始终启用，作为补充）
        if self.vector_retriever.is_applicable(plan):
            tasks["vector"] = self._run_vector_retrieval(query, plan, schema, collection_id, top_k)

        # 关键词检索（可选）
        if self.enable_keyword_search and self.keyword_retriever.is_applicable(plan):
            tasks["keyword"] = self._run_keyword_retrieval(query, plan, schema, top_k)

        if not tasks:
            return result

        # 并行执行
        task_names = list(tasks.keys())
        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        # 收集结果
        sql_hits = []
        vector_hits = []
        keyword_hits = []

        for name, res in zip(task_names, results):
            if isinstance(res, Exception):
                logger.warning(f"{name} retrieval failed: {res}")
                if name == "sql":
                    result.sql_success = False
                    result.sql_error = str(res)
                elif name == "vector":
                    result.vector_success = False
                    result.vector_error = str(res)
                elif name == "keyword":
                    result.keyword_success = False
                    result.keyword_error = str(res)
                continue

            if name == "sql":
                sql_hits, elapsed = res
                result.sql_hits = sql_hits
                result.sql_time_ms = elapsed
            elif name == "vector":
                vector_hits, elapsed = res
                result.vector_hits = vector_hits
                result.vector_time_ms = elapsed
            elif name == "keyword":
                keyword_hits, elapsed = res
                result.keyword_hits = keyword_hits
                result.keyword_time_ms = elapsed

        # 结果融合
        if sql_hits or vector_hits or keyword_hits:
            fused = self.fusion.fuse(
                sql_hits=sql_hits,
                vector_hits=vector_hits,
                keyword_hits=keyword_hits,
                top_k=top_k,
            )
            result.merged_hits = fused.merged_hits

        return result

    async def _parallel_retrieval_multi_table(
        self,
        query: str,
        plan: QueryPlan,
        table_schemas: list,
        collection_id: str,
        top_k: int,
        inherited_state: dict | None = None,
    ) -> MultiRetrievalResult:
        """
        多表并行检索：对每个候选表分别做 SQL 检索，再加上向量/关键词检索

        Args:
            query: 用户查询
            plan: 主表的查询计划（用于向量/关键词检索和向后兼容）
            table_schemas: list of (file_info, TableSchema)
            collection_id: 知识库ID
            top_k: 每路返回的 top_k

        Returns:
            MultiRetrievalResult，sql_hits 包含所有表的 SQL 结果
        """
        result = MultiRetrievalResult()
        primary_schema = table_schemas[0][1]

        # 如果只有一个表，走单表逻辑（保持原有行为）
        if len(table_schemas) <= 1:
            single_result = await self._parallel_retrieval(
                query=query,
                plan=plan,
                schema=primary_schema,
                collection_id=collection_id,
                top_k=top_k,
            )
            for hit in single_result.sql_hits:
                hit.metadata["table_name"] = table_schemas[0][0].get("file_name", "")
                hit.metadata["collection_id"] = table_schemas[0][0].get("collection_id", "")
            return single_result

        # --- 多表并行 SQL 检索 ---
        sql_tasks = []
        sql_table_names = []
        for file_info, schema in table_schemas:
            # 每个表独立做意图分析和 SQL 检索
            async def _table_sql(fi, sch):
                try:
                    t_start = time.time()
                    # 每个表独立分析
                    t_plan = await self.router_agent.analyze(
                        query=query,
                        file_path=fi["file_path"],
                        sheet_name=fi.get("sheet_name", ""),
                        schema=sch,
                        inherited_state=inherited_state,
                    )
                    # 分组聚合：单表不做 limit 截断，交由跨表合并后统一取 TopN，
                    # 否则各表只取到本表 TopN，合并后会漏掉全局最优分组
                    if t_plan.operation == QueryOperation.GROUP_AGG:
                        t_plan.limit = 0
                    # 只有结构化查询且置信度足够才跑 SQL
                    use_sql = (
                        t_plan.needs_sql
                        and t_plan.confidence >= self.sql_min_confidence
                        and self.sql_retriever.is_applicable(t_plan)
                    )
                    if not use_sql:
                        return [], 0, fi.get("file_name", "")
                    hits = await self.sql_retriever.retrieve(query, t_plan, sch, top_k)
                    # 给每个 hit 加上来源表标识
                    table_name = fi.get("file_name", "")
                    for h in hits:
                        h.metadata["table_name"] = table_name
                        h.metadata["collection_name"] = fi.get("collection_name", "")
                        h.metadata["collection_id"] = fi.get("collection_id", "")
                        h.metadata["document_id"] = fi.get("document_id", "")
                    elapsed = (time.time() - t_start) * 1000
                    return hits, elapsed, table_name
                except Exception as e:
                    logger.warning(f"SQL retrieval for table failed: {e}")
                    return [], 0, fi.get("file_name", "")

            sql_tasks.append(_table_sql(file_info, schema))
            sql_table_names.append(file_info.get("file_name", ""))

        # --- 向量检索（跨库，用主表 plan） ---
        vector_task = None
        if self.vector_retriever.is_applicable(plan):
            vector_task = self._run_vector_retrieval(
                query, plan, primary_schema, collection_id, top_k
            )

        # --- 关键词检索（用主表 schema 和 plan） ---
        keyword_task = None
        if self.enable_keyword_search and self.keyword_retriever.is_applicable(plan):
            keyword_task = self._run_keyword_retrieval(query, plan, primary_schema, top_k)

        # --- 并行执行 ---
        all_sql_hits = []
        sql_total_time = 0.0
        sql_has_error = False

        # 等待所有 SQL 任务
        sql_results = await asyncio.gather(*sql_tasks, return_exceptions=True)
        tables_with_data = []
        for res in sql_results:
            if isinstance(res, Exception):
                sql_has_error = True
                continue
            hits, elapsed, tname = res
            # 过滤空结果：如果所有 hit 都是 empty，说明这个表没有相关数据
            has_valid_data = False
            for h in hits:
                if h.payload and h.payload.get("empty"):
                    continue
                # 聚合结果为 0 且有过滤条件时，也视为无匹配数据
                if (
                    h.payload
                    and h.payload.get("type") == "aggregate"
                    and h.payload.get("value") in (0, 0.0, "0")
                    and h.payload.get("filters")
                ):
                    continue
                if h.content:
                    has_valid_data = True
                    break
            if has_valid_data:
                all_sql_hits.extend(hits)
                tables_with_data.append(tname)
            sql_total_time += elapsed

        result.sql_hits = all_sql_hits
        result.sql_success = not sql_has_error and len(all_sql_hits) > 0
        result.sql_time_ms = sql_total_time

        # 向量和关键词检索
        vector_hits = []
        keyword_hits = []

        if vector_task:
            try:
                vres = await vector_task
                vector_hits, v_elapsed = vres
                result.vector_hits = vector_hits
                result.vector_time_ms = v_elapsed
                result.vector_success = True
            except Exception as e:
                result.vector_success = False
                result.vector_error = str(e)

        if keyword_task:
            try:
                kres = await keyword_task
                keyword_hits, k_elapsed = kres
                result.keyword_hits = keyword_hits
                result.keyword_time_ms = k_elapsed
                result.keyword_success = True
            except Exception as e:
                result.keyword_success = False
                result.keyword_error = str(e)

        # 多表聚合并：同类型聚合结果跨表合并
        # 例如3个销售表都返回了 SUM(销售金额)，合并成一个总值
        if len(tables_with_data) > 1:
            all_sql_hits = self._merge_aggregate_hits(all_sql_hits)
            # 分组聚合结果跨表合并（同一维度值求和后再全局排名，最后按 limit 截断）
            all_sql_hits = self._merge_group_agg_hits(all_sql_hits, limit=plan.limit or 0)

        # 结果融合（SQL 结果按来源表分组后再融合）
        if all_sql_hits or vector_hits or keyword_hits:
            fused = self.fusion.fuse(
                sql_hits=all_sql_hits,
                vector_hits=vector_hits,
                keyword_hits=keyword_hits,
                top_k=top_k,
            )
            result.merged_hits = fused.merged_hits

        # 标记是否为多表结果（供 synthesizer 判断走什么路径）
        result.metadata = {
            "multi_table": len(tables_with_data) > 1,
            "tables_queried": sql_table_names,
            "tables_with_data": tables_with_data,
        }

        return result

    async def _run_sql_retrieval(self, query, plan, schema, top_k):
        """执行 SQL 检索（带计时）"""
        start = time.time()
        try:
            hits = await self.sql_retriever.retrieve(query, plan, schema, top_k)
            elapsed = (time.time() - start) * 1000
            return hits, elapsed
        except Exception as e:
            logger.error(f"SQL retrieval error: {e}")
            raise

    async def _run_vector_retrieval(self, query, plan, schema, collection_id, top_k):
        """执行向量检索（带计时）"""
        start = time.time()
        try:
            hits = await self.vector_retriever.retrieve(
                query=query,
                plan=plan,
                schema=schema,
                top_k=top_k,
                collection_id=collection_id,
            )
            elapsed = (time.time() - start) * 1000
            return hits, elapsed
        except Exception as e:
            logger.error(f"Vector retrieval error: {e}")
            raise

    async def _run_keyword_retrieval(self, query, plan, schema, top_k):
        """执行关键词检索（带计时）"""
        start = time.time()
        try:
            hits = await self.keyword_retriever.retrieve(query, plan, schema, top_k)
            elapsed = (time.time() - start) * 1000
            return hits, elapsed
        except Exception as e:
            logger.error(f"Keyword retrieval error: {e}")
            raise

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    async def _load_schema(self, target_file: dict) -> TableSchema | None:
        """加载表结构（带错误处理）"""
        try:
            schema = self.schema_provider.get_schema(
                target_file["file_path"],
                target_file.get("sheet_name", ""),
            )
            # 补充 document_id
            schema.document_id = target_file.get("document_id", "")
            return schema
        except Exception as e:
            logger.warning(f"Failed to load schema: {e}")
            return None

    async def _vector_only_path(
        self, context: AgentContext, query: str, collection_id: str, top_k: int
    ) -> None:
        """纯向量检索路径（降级用）"""
        start = time.time()

        try:
            plan = QueryPlan(
                intent=QueryIntent.SEMANTIC,
                operation=QueryOperation.UNKNOWN,
                needs_vector=True,
                needs_sql=False,
                reasoning="纯向量检索模式",
                confidence=0.5,
            )

            hits = await self.vector_retriever.retrieve(
                query=query,
                plan=plan,
                schema=TableSchema(table_name="all", document_id=collection_id),
                top_k=top_k,
                collection_id=collection_id,
            )
            elapsed = (time.time() - start) * 1000

            multi = MultiRetrievalResult(
                vector_hits=hits,
                merged_hits=hits,
                vector_success=bool(hits),
                vector_time_ms=elapsed,
            )
            context.multi_retrieval = multi
            context.used_vector = bool(hits)
            context.query_plan = plan

            # 兼容旧字段
            context.retrieval_result = self._hits_to_retrieval_result(hits)

            # 合成答案
            if hits:
                answer, references = await self.synthesizer_agent.synthesize_multi(
                    query=query,
                    plan=plan,
                    multi_result=multi,
                    context=context,
                )
                context.final_answer = answer
                context.references = references
                context.is_kb_answer = True
            else:
                context.final_answer = "未找到相关结果。"
                context.is_kb_answer = False

        except Exception as e:
            logger.error(f"Vector-only path failed: {e}")
            context.final_answer = "未找到相关结果。"
            context.is_kb_answer = False

    async def _multi_query_path(
        self,
        context: AgentContext,
        decomposition,
        effective_collection: str,
        top_k: int,
        session_id: str,
        history_messages: list,
        inherited_state: dict | None = None,
    ) -> None:
        """
        多意图查询路径：多个子查询并行执行，结果合并后综合回答

        Args:
            context: 上下文（写入最终结果）
            decomposition: 分解结果（DecompositionResult）
            effective_collection: 有效的知识库ID
            top_k: 每路检索的 top_k
            session_id: 会话ID
            history_messages: 历史消息
        """

        sub_queries = decomposition.sub_queries

        # Step 1: 查找并加载表格文件（共享，只做一次）
        table_files = await self._find_table_files(effective_collection)
        all_sql_hits = []
        all_vector_hits = []
        collected_filters: list[dict] = []
        collected_scope: dict = {}

        if not table_files:
            # 没有表格文件：每个子查询都走向量检索
            logger.info("No table files in multi-query path, using vector search only")
            tasks = [
                self.vector_retriever.retrieve(
                    query=sq.query,
                    plan=None,
                    collection_id=effective_collection,
                    top_k=top_k,
                    session_id=session_id,
                )
                for sq in sub_queries
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for i, res in enumerate(results):
                if isinstance(res, Exception):
                    logger.warning(f"Sub-query #{i} vector search failed: {res}")
                    continue
                if res:
                    all_vector_hits.extend(res)
        else:
            # 有表格文件：加载所有 schema（共享，只做一次）
            # 注意：不要在这里用整体查询做 _rank_table_files 过滤！
            # 因为多子任务场景下，每个子查询可能需要不同的表（例如"王强的信息"要员工表，
            # "销售金额"要销售表），如果按整体查询预排名丢弃了员工表，
            # 子任务 1 就永远查不到部门信息了。
            # 保留全部表，让每个子任务在 _run_sub_query 里独立排名、各自筛选。

            all_table_schemas = []  # list of (file_info, schema)
            for f in table_files:
                try:
                    schema = await self._load_schema(f)
                    if schema:
                        all_table_schemas.append((f, schema))
                except Exception as e:
                    logger.warning(f"Failed to load schema for {f.get('file_name', '?')}: {e}")

            if not all_table_schemas:
                all_table_schemas = []

            # 预提取所有文件名和 schema，供每个子查询独立排名使用
            all_files = [f for f, _ in all_table_schemas]
            all_schemas = [s for _, s in all_table_schemas]
            logger.info(
                f"[多子任务] 加载到 {len(all_table_schemas)} 张可用表："
                f"{[f['file_name'] for f in all_files]}"
            )

            # Step 2: 每个子查询并行执行完整检索流程
            # 每个子查询独立做表格排名 + 路由分析 + SQL检索 + 向量检索
            async def _run_sub_query(sub_q, idx: int = 0) -> tuple[list, list]:
                """执行单个子查询，返回 (sql_hits, vector_hits)"""
                tag = f"[子任务{idx + 1}]「{sub_q.query}」"
                try:
                    # === 关键修复：每个子查询独立做表格排名 ===
                    # 使用子查询自身的语义去匹配最相关的表，
                    # 而不是沿用整体查询的排名（整体查询可能偏向某一类表）
                    sub_ranked_files = self._rank_table_files(all_files, sub_q.query, all_schemas)
                    # 根据排名后的文件顺序重新组装 (file, schema) 对
                    file_id_map = {id(f): (f, s) for f, s in zip(all_files, all_schemas)}
                    sub_ranked_schemas = [
                        file_id_map[id(f)] for f in sub_ranked_files if id(f) in file_id_map
                    ]

                    logger.info(
                        f"{tag} 选表排名（共 {len(sub_ranked_schemas)} 张）："
                        f"{[f['file_name'] for f, _ in sub_ranked_schemas]}"
                    )

                    # ===== 修复A：每张候选表各自做意图分析 =====
                    # 原因：不同表对同一个实体可能用不同列表达（例如"王强"在销售表是"销售经理"列，
                    # 在员工表是"姓名"列）。如果复用主表 plan 的过滤列，会导致其他表
                    # 要么过滤列不存在被静默丢弃，要么匹配不到正确数据。
                    # 这里先对所有候选表并行做 analyze，得到各自专属的 plan。

                    # 决定查询多少个表（多意图场景下表数适当增加，避免漏掉跨表信息）
                    max_tables = min(
                        len(sub_ranked_schemas), DEFAULT_MAX_TABLES_PER_SUBQUERY
                    )  # 先按默认上限做分析
                    selected_schemas = sub_ranked_schemas[:max_tables]

                    # 并行对每张候选表做意图分析
                    per_table_plans: dict[int, QueryPlan] = {}

                    # ---- 预过滤：先用规则法生成计划，高置信度的直接跳过 LLM ----
                    # 目的：减少 per-table LLM 调用次数，降低延迟和成本
                    # 阈值：>= 0.7 认为高置信度，直接用规则结果
                    RULE_PLAN_THRESHOLD = 0.7

                    llm_needed: list[tuple] = []  # 需要 LLM analyze 的表
                    for file_info, schema in selected_schemas:
                        rule_plan, rule_conf = self._rule_based_table_plan(
                            query=sub_q.query,
                            schema=schema,
                        )
                        if rule_plan and rule_conf >= RULE_PLAN_THRESHOLD:
                            per_table_plans[id(file_info)] = rule_plan
                            logger.info(
                                f"{tag} 表「{file_info.get('file_name', '')}」规则法规划："
                                f"intent={rule_plan.intent.value}, op={rule_plan.operation.value}, "
                                f"target={rule_plan.target_column}, confidence={rule_conf:.2f} (跳过LLM)"
                            )
                        else:
                            llm_needed.append((file_info, schema))

                    # ---- 对低置信度的表走 LLM analyze ----
                    analyze_tasks = []
                    for file_info, schema in llm_needed:
                        analyze_tasks.append(
                            self.router_agent.analyze(
                                query=sub_q.query,
                                file_path=file_info["file_path"],
                                sheet_name=file_info.get("sheet_name", ""),
                                schema=schema,
                                inherited_state=inherited_state,
                            )
                        )

                    if analyze_tasks:
                        analyze_results = await asyncio.gather(
                            *analyze_tasks, return_exceptions=True
                        )
                        for (file_info, _schema), a_res in zip(llm_needed, analyze_results):
                            if isinstance(a_res, Exception):
                                logger.warning(
                                    f"{tag} 表「{file_info.get('file_name', '?')}」分析失败: {a_res}"
                                )
                                continue
                            per_table_plans[id(file_info)] = a_res
                            logger.info(
                                f"{tag} 表「{file_info.get('file_name', '')}」分析："
                                f"intent={a_res.intent.value}, op={a_res.operation.value}, "
                                f"target={a_res.target_column}, "
                                f"filters={[(f.column, f.value) for f in a_res.filters]}, "
                                f"confidence={a_res.confidence:.2f}"
                            )

                    # 主表 = 排名第一且分析成功的那张；用于向量检索、语义类型选表、
                    # 回传会话级 filters/scope 等"非查表 SQL"的用途
                    primary_plan = None
                    primary_schema_item = None
                    primary_file_info = None
                    if selected_schemas:
                        primary_file_info, primary_schema_item = selected_schemas[0]
                        primary_plan = per_table_plans.get(id(primary_file_info))

                    # 按主表 plan 用语义类型驱动重新选表
                    # （比固定 top-N 更精准，能覆盖所有包含相关语义类型列的表）
                    # 兜底表 20 张（准确性优先，宁可多查也不漏）
                    if primary_plan and primary_schema_item:
                        semantic_selected = self._select_semantically_relevant_tables(
                            plan=primary_plan,
                            primary_schema=primary_schema_item,
                            all_schemas=sub_ranked_schemas,
                            min_fallback=20,
                        )
                        # 找出新增的表（语义类型选出来但不在 selected_schemas 里的）
                        selected_ids = {id(f) for f, _ in selected_schemas}
                        extra = [
                            (f, s) for f, s in semantic_selected
                            if id(f) not in selected_ids
                        ]

                        if extra:
                            logger.info(
                                f"{tag} 语义类型扩展：新增 {len(extra)} 张表 "
                                f"({', '.join(f.get('file_name', '?') for f, _ in extra[:3])}"
                                f"{'...' if len(extra) > 3 else ''})"
                            )

                            # 扩展表先走规则法预过滤
                            extra_llm_needed = []
                            for f, s in extra:
                                rule_plan, rule_conf = self._rule_based_table_plan(
                                    query=sub_q.query,
                                    schema=s,
                                )
                                if rule_plan and rule_conf >= RULE_PLAN_THRESHOLD:
                                    per_table_plans[id(f)] = rule_plan
                                    logger.info(
                                        f"{tag} 语义扩展表「{f.get('file_name', '')}」"
                                        f"规则法规划：target={rule_plan.target_column}, "
                                        f"confidence={rule_conf:.2f} (跳过LLM)"
                                    )
                                else:
                                    extra_llm_needed.append((f, s))

                            # 对剩下的低置信度表走 LLM
                            if extra_llm_needed:
                                extra_tasks = [
                                    self.router_agent.analyze(
                                        query=sub_q.query,
                                        file_path=f["file_path"],
                                        sheet_name=f.get("sheet_name", ""),
                                        schema=s,
                                        inherited_state=inherited_state,
                                    )
                                    for f, s in extra_llm_needed
                                ]
                                extra_results = await asyncio.gather(*extra_tasks, return_exceptions=True)
                                for (f, s), er in zip(extra_llm_needed, extra_results):
                                    if isinstance(er, Exception):
                                        continue
                                    per_table_plans[id(f)] = er
                                    logger.info(
                                        f"{tag} 语义扩展表「{f.get('file_name', '')}」分析："
                                        f"filters={[(x.column, x.value) for x in er.filters]}"
                                    )

                        # 更新 selected_schemas 为语义类型选出的全部表
                        # （保持原排名顺序）
                        selected_schemas = semantic_selected
                    else:
                        primary_plan = QueryPlan(
                            operation=QueryOperation.SEMANTIC,
                            intent=QueryIntent.SEMANTIC,
                            needs_sql=True,
                            needs_vector=True,
                            confidence=0.5,
                        )

                    # 多表并行 SQL 检索（每张表使用各自专属的 plan）
                    sub_sql_hits = []
                    sql_tasks = []
                    for file_info, schema in selected_schemas:
                        table_plan = per_table_plans.get(id(file_info), primary_plan)
                        sql_tasks.append(
                            self._run_sql_retrieval(
                                query=sub_q.query,
                                plan=table_plan,
                                schema=schema,
                                top_k=top_k,
                            )
                        )

                    if sql_tasks:
                        sql_results = await asyncio.gather(*sql_tasks, return_exceptions=True)
                        for (file_info, schema), sql_res in zip(selected_schemas, sql_results):
                            if isinstance(sql_res, Exception):
                                logger.warning(
                                    f"SQL retrieval failed for {file_info.get('file_name', '?')}: {sql_res}"
                                )
                                continue
                            # sql_res 是 (hits, elapsed_ms) 元组
                            hits, _ = sql_res
                            if hits:
                                for hit in hits:
                                    hit.payload["source_file"] = file_info.get("file_name", "")
                                    hit.payload["sub_query"] = sub_q.query
                                sub_sql_hits.extend(hits)
                            logger.info(
                                f"{tag} 查表「{file_info.get('file_name', '?')}」SQL 命中 {len(hits)} 条"
                                f"，样例：{self._brief_sql_hits(hits)}"
                            )

                    # 向量检索
                    sub_vector_hits = []
                    try:
                        vec_hits, _ = await self._run_vector_retrieval(
                            query=sub_q.query,
                            plan=primary_plan,
                            schema=primary_schema_item if primary_schema_item else None,
                            collection_id=effective_collection,
                            top_k=top_k,
                        )
                        if vec_hits:
                            # 修复 B：给向量命中补上 sub_query 标记
                            # 之前 SQL 命中打了 sub_query，但向量命中没打，
                            # 导致合成时向量召回的内容全部落到"未知"分组，
                            # 按子问题组织答案时会漏掉这部分信息。
                            for vh in vec_hits:
                                if hasattr(vh, "payload") and isinstance(vh.payload, dict):
                                    vh.payload["sub_query"] = sub_q.query
                                elif hasattr(vh, "payload"):
                                    # 兼容非 dict payload 的情况（如对象）
                                    try:
                                        vh.payload.sub_query = sub_q.query
                                    except Exception:
                                        pass
                            sub_vector_hits = vec_hits
                    except Exception as e:
                        logger.warning(f"Vector retrieval failed for sub-query: {e}")

                    logger.info(
                        f"{tag} 检索完成：SQL {len(sub_sql_hits)} 条，向量 {len(sub_vector_hits)} 条"
                    )

                    # 回传该子查询生效的条件（供会话范围持久化）
                    sub_plan_filters: list[dict] = []
                    if primary_plan is not None:
                        sub_plan_filters = [
                            {
                                "column": f.column,
                                "value": f.value,
                                "operator": f.operator,
                                "column_alias": f.column_alias,
                                "source": getattr(f, "source", "explicit"),
                                "turn": getattr(f, "turn", 0),
                                "explicit": getattr(f, "explicit", True),
                                "confidence": getattr(f, "confidence", 1.0),
                            }
                            for f in primary_plan.filters
                        ]
                    # 维度槽位（分组聚合时）
                    sub_scope: dict = {}
                    if (
                        primary_plan is not None
                        and primary_plan.operation == QueryOperation.GROUP_AGG
                    ):
                        sub_scope = {
                            "group_by": primary_plan.group_by,
                            "metric": primary_plan.metric_column,
                            "order_dir": primary_plan.order_dir,
                            "limit": primary_plan.limit,
                        }
                    return sub_sql_hits, sub_vector_hits, sub_plan_filters, sub_scope

                except Exception as e:
                    logger.error(f"Sub-query '{sub_q.query[:30]}...' failed: {e}")
                    return [], [], [], {}

            # 并行执行所有子查询
            tasks = [_run_sub_query(sq, i) for i, sq in enumerate(sub_queries)]
            results = await asyncio.gather(*tasks)

            for i, (sql_hits, vec_hits, sub_filters, sub_scope) in enumerate(results):
                all_sql_hits.extend(sql_hits)
                all_vector_hits.extend(vec_hits)
                collected_filters.extend(sub_filters)
                if sub_scope and not collected_scope:
                    collected_scope = sub_scope
                logger.info(
                    f"[子任务{i + 1}]「{sub_queries[i].query}」汇总："
                    f"SQL {len(sql_hits)} 条，向量 {len(vec_hits)} 条，"
                    f"filters={[(f['column'], f['value']) for f in sub_filters]}"
                )

        # Step 3: 合并结果
        # 给所有 hit 加上子查询标记，便于合成器区分来源
        all_hits = all_sql_hits + all_vector_hits

        # Step 4: 多子查询合成回答
        try:
            final_answer = await self.synthesizer_agent.synthesize_multi_subquery(
                query=context.query,
                sub_queries=[sq.query for sq in sub_queries],
                sql_hits=all_sql_hits,
                vector_hits=all_vector_hits,
            )
            context.final_answer = final_answer
            context.is_kb_answer = True
        except Exception as e:
            logger.error(f"Multi-query synthesis failed: {e}")
            # 降级：用所有 hit 走普通合成（synthesize_from_hits 内部完成 hit → 结果对象转换）
            try:
                fallback_plan = context.query_plan
                if fallback_plan is None:
                    fallback_plan = QueryPlan(
                        intent=QueryIntent.SEMANTIC,
                        operation=QueryOperation.UNKNOWN,
                        needs_sql=False,
                        needs_vector=True,
                        reasoning="多意图降级合成",
                        confidence=0.5,
                    )
                answer, _refs = await self.synthesizer_agent.synthesize_from_hits(
                    query=context.query,
                    plan=fallback_plan,
                    sql_hits=all_sql_hits,
                    vector_hits=all_vector_hits,
                    context=context,
                )
                context.final_answer = answer
                context.is_kb_answer = True
            except Exception as e2:
                logger.error(f"Fallback synthesis also failed: {e2}")
                context.final_answer = "抱歉，处理您的问题时出现错误。"
                context.is_kb_answer = False

        # 记录调试信息
        context.debug_info["sql_hits_count"] = len(all_sql_hits)
        context.debug_info["vector_hits_count"] = len(all_vector_hits)
        context.sql_hits = all_sql_hits
        context.vector_hits = all_vector_hits

        # 多意图路径同样回传生效条件（按列+值去重），供上层持久化为会话范围
        seen_keys: set = set()
        merged_filters: list[dict] = []
        for f in collected_filters:
            key = (f.get("column"), f.get("value"))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            merged_filters.append(f)
        context.effective_filters = merged_filters
        context.effective_scope = collected_scope

        # 兼容旧字段：设置 sql_result / used_sql / used_vector
        # 确保 chat_service 等调用方能正确判断是否使用 SmartQuery 结果
        context.used_sql = bool(all_sql_hits)
        context.used_vector = bool(all_vector_hits)
        if all_sql_hits:
            # 多子查询场景：用第一个聚合 hit 或列表 hit 构造 SQLQueryResult
            # （chat_service 只需要 success=True 且 rows 非空来判断是否启用）
            try:
                from .types import SQLQueryResult as _SQLResult

                agg_hits = [h for h in all_sql_hits if h.payload.get("type") == "aggregate"]
                list_hits = [h for h in all_sql_hits if h.payload.get("type") != "aggregate"]
                if agg_hits:
                    # 取第一个聚合值作为代表
                    h = agg_hits[0]
                    col = h.payload.get("target_column", "结果")
                    context.sql_result = _SQLResult(
                        success=True,
                        sql="",
                        columns=[col],
                        rows=[[h.payload.get("value", 0)]],
                        row_count=1,
                    )
                elif list_hits:
                    # 列表类：从第一个 hit 推断列
                    sample = list_hits[0].payload or {}
                    cols = [
                        k
                        for k in sample.keys()
                        if k
                        not in (
                            "type",
                            "operation",
                            "source_table",
                            "sub_query",
                            "document_id",
                            "total_count",
                        )
                    ][:10]
                    rows = []
                    for h in list_hits[:100]:  # 最多100行用于兼容
                        row = [h.payload.get(c, "") for c in cols]
                        rows.append(row)
                    context.sql_result = _SQLResult(
                        success=True,
                        columns=cols,
                        rows=rows,
                        row_count=len(list_hits),
                    )
            except Exception as e:
                logger.warning(f"Failed to build sql_result for multi-query: {e}")

        # 记录子查询分解日志（详细）
        sub_q_info = ", ".join(f"[{i + 1}] {sq.query}" for i, sq in enumerate(sub_queries))
        logger.info(
            f"Multi-query decomposition complete: "
            f"method={decomposition.method}, "
            f"sub_queries=[{sub_q_info}], "
            f"sql_hits={len(all_sql_hits)}, "
            f"vector_hits={len(all_vector_hits)}"
        )

    def _hits_to_sql_result(self, hits, plan: QueryPlan) -> SQLQueryResult:
        """将 RetrievalHit 转换为 SQLQueryResult（兼容旧接口）"""
        if not hits:
            return SQLQueryResult(success=False, error="no results")

        # 聚合查询
        if hits and hits[0].payload.get("type") == "aggregate":
            return SQLQueryResult(
                success=True,
                sql="",
                columns=[plan.target_column_alias or plan.target_column or "结果"],
                rows=[[hits[0].payload.get("value", 0)]],
                row_count=1,
            )

        # 列表查询
        if hits and hits[0].payload and isinstance(hits[0].payload, dict):
            # 从第一个 hit 的 payload 推断列名
            sample_data = hits[0].payload
            if (
                sample_data
                and isinstance(sample_data, dict)
                and not sample_data.get("type") == "aggregate"
            ):
                columns = list(sample_data.keys())[:10]
                rows = []
                for hit in hits:
                    row = [hit.payload.get(c, "") for c in columns]
                    rows.append(row)
                return SQLQueryResult(
                    success=True,
                    columns=columns,
                    rows=rows,
                    row_count=len(hits),
                )

        return SQLQueryResult(
            success=True, columns=["content"], rows=[[h.content] for h in hits], row_count=len(hits)
        )

    def _hits_to_retrieval_result(self, hits) -> RetrievalQueryResult:
        """将 RetrievalHit 转换为 RetrievalQueryResult（兼容旧接口）"""
        results = []
        max_score = 0.0
        for hit in hits:
            results.append(
                {
                    "chunk_id": hit.record_id,
                    "document_id": hit.document_id,
                    "document_name": hit.payload.get("document_name") or hit.payload.get("source_file") or hit.metadata.get("table_name") or "未知文档",
                    "collection_id": hit.payload.get("collection_id") or hit.metadata.get("collection_id", ""),
                    "text": hit.content,
                    "score": hit.score,
                    "page_num": hit.metadata.get("page_num", 0),
                    "sheet_name": hit.sheet_name,
                    "row_number": hit.row_number,
                }
            )
            if hit.score > max_score:
                max_score = hit.score

        return RetrievalQueryResult(
            success=bool(hits),
            results=results,
            total=len(hits),
            max_score=max_score,
        )

    @staticmethod
    def _brief_sql_hits(hits: list, limit: int = BRIEF_SQL_HITS_LIMIT) -> str:
        """为日志生成 SQL 命中结果的紧凑摘要（最多展示 limit 条）"""
        if not hits:
            return "无"
        internal = {
            "type",
            "operation",
            "sub_query",
            "source_file",
            "document_id",
            "source_table",
            "_total_count",
            "_shown_count",
            "empty",
        }
        briefs = []
        for h in hits[:limit]:
            p = getattr(h, "payload", None) or {}
            htype = p.get("type", "")
            if htype == "aggregate":
                briefs.append(
                    f"聚合{str(p.get('operation', '')).upper()}"
                    f"({p.get('target_column', '')})={p.get('value', '')}"
                )
            elif htype == "distinct_list":
                briefs.append(f"去重列「{p.get('column', '')}」={p.get('value', '')}")
            else:
                cols = {k: v for k, v in p.items() if k not in internal}
                item = "，".join(f"{k}={v}" for k, v in list(cols.items())[:6])
                briefs.append(item or (getattr(h, "content", "") or "")[:80])
        more = f" …（共 {len(hits)} 条）" if len(hits) > limit else ""
        return " | ".join(briefs) + more

    # ------------------------------------------------------------------
    # 智能 top_k 解析
    # ------------------------------------------------------------------

    def _resolve_top_k(self, requested_top_k: int, plan) -> int:
        """
        解析最终使用的 top_k 值。

        优先级：
        1. plan.suggested_top_k（RouterAgent 根据查询类型推荐的）
        2. requested_top_k（前端传入的）
        3. 默认值 self.default_top_k

        同时设置合理的上下限，防止极端值。
        """
        # 默认值兜底
        default_k = self.default_top_k or 10

        # 取 RouterAgent 推荐的值（如果有）
        suggested = getattr(plan, "suggested_top_k", 0) or 0

        if suggested > 0:
            # 有推荐值：使用推荐值，但受上限约束
            result = suggested
        elif requested_top_k and requested_top_k > 0:
            # 没有推荐值，使用请求传入的值
            result = requested_top_k
        else:
            result = default_k

        # 上下限约束
        # 下限：1（至少返回1条）
        # 上限：100（避免上下文爆炸，同时满足大多数列表查询需求）
        # 聚合类查询上限可以更高（因为 SQL 返回只有1条聚合结果，明细有限）
        op = getattr(plan, "operation", None)
        if op and op in (QueryOperation.SUM, QueryOperation.AVG, QueryOperation.COUNT):
            max_k = SQL_DETAIL_MAX_K_AGG  # 聚合类：明细只取前K条做展示
        else:
            max_k = SQL_DETAIL_MAX_K_DEFAULT

        result = max(1, min(result, max_k))
        return result

    def _select_semantically_relevant_tables(
        self,
        plan,
        primary_schema,
        all_schemas: list,
        min_fallback: int = 3,
    ) -> list:
        """
        语义类型驱动选表：根据查询涉及的语义类型，选择所有相关表，
        而不是简单的 top-N 截断。

        核心逻辑：
        1. 从 plan（基于主表分析）中提取涉及的语义类型：
           - target_column 的语义类型
           - 每个 filter 列的语义类型
        2. 在 all_schemas 中找出所有包含这些语义类型列的表
        3. 加上排名靠前的 min_fallback 张表做兜底（防止语义类型判断不准）
        4. 保持原有排名顺序

        Args:
            plan: QueryPlan（基于主表分析的结果）
            primary_schema: 主表的 TableSchema（用于查找列的 semantic_type）
            all_schemas: 所有候选表的 (file_info, schema) 列表（已按排名排序）
            min_fallback: 兜底表数量（至少选多少张排名靠前的表）

        Returns:
            选中的 (file_info, schema) 列表（保持原排名顺序）
        """
        if not all_schemas:
            return []

        # ---- 1. 提取查询涉及的语义类型 ----
        target_sem_types: set[str] = set()

        # target_column 的语义类型
        target_col = getattr(plan, "target_column", "")
        if target_col and primary_schema:
            col = primary_schema.get_column(target_col)
            if col and col.semantic_type and col.semantic_type != "other":
                target_sem_types.add(col.semantic_type)

        # filter 各列的语义类型
        filters = getattr(plan, "filters", []) or []
        for flt in filters:
            col_name = getattr(flt, "column", "")
            if not col_name or not primary_schema:
                continue
            col = primary_schema.get_column(col_name)
            if col and col.semantic_type and col.semantic_type != "other":
                target_sem_types.add(col.semantic_type)

        # 如果没有提取到明确语义类型，退回到 top-N
        if not target_sem_types:
            max_tables = self._resolve_max_tables(plan, len(all_schemas))
            return all_schemas[:max_tables]

        # ---- 2. 按语义类型筛选表 ----
        selected_ids: set[int] = set()
        selected: list = []

        for f, schema in all_schemas:
            schema_id = id(f)
            # 检查这张表是否包含目标语义类型的列
            has_relevant_col = any(
                col.semantic_type in target_sem_types
                for col in schema.columns
                if col.semantic_type and col.semantic_type != "other"
            )
            if has_relevant_col:
                if schema_id not in selected_ids:
                    selected_ids.add(schema_id)
                    selected.append((f, schema))

        # ---- 3. 兜底：保留排名前 min_fallback 张表（防止语义类型判断不准） ----
        # 逻辑：最终选中 = 语义相关表 ∪ top-min_fallback 张表
        # 这样即使语义判断有误，至少不会丢掉排名最靠前的表
        for f, schema in all_schemas[:min_fallback]:
            schema_id = id(f)
            if schema_id not in selected_ids:
                selected_ids.add(schema_id)
                selected.append((f, schema))

        # ---- 4. 按原排名顺序重新排列 ----
        # （保持原排名顺序，语义相关 + 兜底的都在里面，但顺序不变）
        rank_order = {id(f): idx for idx, (f, _) in enumerate(all_schemas)}
        selected.sort(key=lambda item: rank_order.get(id(item[0]), len(all_schemas)))

        return selected

    def _resolve_max_tables(self, plan, total_tables: int) -> int:
        """
        根据查询类型决定最多查多少个表。

        生产级策略：
        - 聚合类（SUM/AVG/COUNT）：需要统计完整性，查所有表（上限 50，防止极端情况）
        - 明细类（LIST/DETAIL/GROUP_COUNT）：
          - 如果有实体过滤条件（filters 非空），查所有表（信息可能散落在多张表）
          - 否则 top-5 够了，足够覆盖主要数据源
        - TOP_N：top-5 够了
        - EXISTS：top-1，找到即止
        - 语义类/未知：用 max_sql_files（默认 3）
        """
        op = getattr(plan, "operation", None)
        intent = getattr(plan, "intent", None)
        filters = getattr(plan, "filters", []) or []
        has_entity_filter = len(filters) > 0

        if op in (QueryOperation.SUM, QueryOperation.AVG, QueryOperation.COUNT):
            # 聚合类：查所有表（统计完整性优先）
            return min(total_tables, 50)

        if op == QueryOperation.GROUP_AGG:
            # 分组聚合类：查所有表（跨表分组求和需要完整数据）
            return min(total_tables, 50)

        if op == QueryOperation.EXISTS:
            # 存在性：1 个表就够了
            return min(total_tables, 1)

        if op == QueryOperation.DETAIL:
            # 详情类：如果有明确实体过滤（如"王强的信息"），信息可能散落在多张表，
            # 应该查所有含该实体的表；否则用默认 3~5 张
            if has_entity_filter:
                return min(total_tables, 50)
            return min(total_tables, max(self.max_sql_files, 5))

        if op in (
            QueryOperation.LIST,
            QueryOperation.GROUP_COUNT,
            QueryOperation.TOP_N,
        ):
            # 列表/分组/排名类：3~5 个表够了（主要数据源一般在 top 里）
            return min(total_tables, max(self.max_sql_files, 5))

        if intent == QueryIntent.SEMANTIC:
            # 语义类：默认值
            return min(total_tables, self.max_sql_files)

        # 兜底：默认 max_sql_files
        return min(total_tables, self.max_sql_files)

    # ------------------------------------------------------------------
    # 规则法表级查询计划（预过滤，减少 per-table LLM 调用）
    # ------------------------------------------------------------------

    def _rule_based_table_plan(
        self,
        query: str,
        schema,
    ) -> tuple[object | None, float]:
        """
        基于规则法快速生成单表查询计划，用于多子任务路径的 per-table 预过滤。

        思路：不调用 LLM，纯用关键词 + 列语义类型 + 列别名匹配，
        快速推断 target_column、filters、operation、intent 等。
        置信度高时可直接替代 per-table LLM analyze，节省 LLM 调用。

        Args:
            query: 子查询问题
            schema: TableSchema 对象

        Returns:
            (plan, confidence) — plan 为 None 表示规则法无法判断，
            confidence 范围 0-1，>=0.7 认为高置信度可直接用
        """
        from .types import (
            FilterInfo,
            QueryIntent,
            QueryOperation,
            QueryPlan,
        )

        query = query.strip()
        if not query:
            return None, 0.0

        columns = schema.columns
        if not columns:
            return None, 0.0

        # ---- 1. 从问题中提取操作类型（count/list/sum/avg/top） ----
        op, op_confidence = self._detect_operation(query)

        # ---- 2. 匹配目标列 ----
        # 从问题中去掉操作类关键词后，剩余内容去匹配列名/别名
        q_clean = self._strip_operation_words(query)

        target_col, target_score = self._match_best_column(q_clean, columns)

        # ---- 3. 匹配过滤列 + 值 ----
        filters = []
        filter_score = 0.0

        # 简单实体提取：从问题中找"是 X 的"、"名为 X 的"、"X 的"这种模式
        # 以及直接将问题中的名词短语与各列样本值做匹配
        entity_candidates = self._extract_entity_candidates(query, columns)

        for col_name, entity_val, match_score in entity_candidates:
            col = schema.get_column(col_name)
            if col is None:
                continue
            # 过滤列不能同时是目标列（除非是不同维度）
            if (
                target_col
                and col.name == target_col.name
                and op in (QueryOperation.COUNT, QueryOperation.LIST, QueryOperation.DETAIL)
            ):
                continue
            flt = FilterInfo(
                column=col.name,
                value=entity_val,
                operator="=",
                column_alias=col.aliases[0] if col.aliases else col.name,
                confidence=match_score,
                source="explicit",
            )
            filters.append(flt)
            filter_score = max(filter_score, match_score)

        # ---- 4. 综合置信度评估 ----
        # 基础分 = 操作类型置信度 * 0.3 + 目标列匹配分 * 0.4 + 过滤匹配分 * 0.3
        confidence = op_confidence * 0.3 + target_score * 0.4 + filter_score * 0.3

        # 如果目标列都没匹配上，置信度直接打低
        if target_col is None:
            confidence = min(confidence, 0.4)

        # ---- 5. 构造 QueryPlan ----
        intent = QueryIntent.STRUCTURED if confidence >= 0.5 else QueryIntent.SEMANTIC
        plan = QueryPlan(
            intent=intent,
            operation=op,
            target_column=target_col.name if target_col else "",
            target_column_alias=(
                target_col.aliases[0]
                if target_col and target_col.aliases
                else (target_col.name if target_col else "")
            ),
            filters=filters,
            needs_sql=confidence >= 0.5,
            needs_vector=confidence < 0.7,  # 置信度不高时同时走向量
            confidence=confidence,
            reasoning=f"rule_based: op={op.value}, target={target_col.name if target_col else 'none'}, "
            f"filters={len(filters)}",
        )

        return plan, confidence

    @staticmethod
    def _detect_operation(query: str) -> tuple:
        """
        从问题中检测操作类型。
        返回 (QueryOperation, confidence)。
        """
        from .types import QueryOperation

        q = query.lower()

        # 计数类
        count_keywords = [
            "多少个",
            "几名",
            "几个",
            "有多少",
            "共多少",
            "数量",
            "几个",
            "多少条",
            "多少人",
            "多少家",
            "多少名",
            "多少种",
            "count",
            "number of",
            "how many",
            "how much",
        ]
        if any(kw in q for kw in count_keywords):
            return QueryOperation.COUNT, 0.85

        # 求和类
        sum_keywords = [
            "总金额",
            "总额",
            "合计",
            "共计",
            "总和",
            "一共多少",
            "总共",
            "总销售额",
            "总销售",
            "总收入",
            "总利润",
            "sum",
            "total",
            "total amount",
        ]
        if any(kw in q for kw in sum_keywords):
            return QueryOperation.SUM, 0.8

        # 平均类
        avg_keywords = [
            "平均",
            "均值",
            "平均值",
            "人均",
            "每单",
            "平均每",
            "avg",
            "average",
            "mean",
        ]
        if any(kw in q for kw in avg_keywords):
            return QueryOperation.AVG, 0.8

        # 排名 / top N
        top_keywords = [
            "排名",
            "最高",
            "最多",
            "最大",
            "前三",
            "前几名",
            "前几位",
            "前 3",
            "top",
            "排行",
            "名列",
        ]
        if any(kw in q for kw in top_keywords):
            return QueryOperation.TOP_N, 0.7

        # 列表 / 哪些
        list_keywords = [
            "哪些",
            "有哪些",
            "都是哪些",
            "分别是",
            "名单",
            "列表",
            "list",
            "which",
            "what are",
        ]
        if any(kw in q for kw in list_keywords):
            return QueryOperation.LIST, 0.7

        # 详情类
        detail_keywords = [
            "信息",
            "详情",
            "详细",
            "资料",
            "档案",
            "detail",
            "info",
            "information",
        ]
        if any(kw in q for kw in detail_keywords):
            return QueryOperation.DETAIL, 0.6

        # 分组统计
        group_keywords = [
            "每个",
            "各",
            "分别",
            "按什么",
            "按哪个",
            "分组",
            "按",
            "per ",
            "by ",
            "group by",
        ]
        if any(kw in q for kw in group_keywords):
            # 分组 + 聚合默认是 GROUP_AGG，具体类型后面再定
            return QueryOperation.GROUP_COUNT, 0.5

        # 默认：未知
        return QueryOperation.UNKNOWN, 0.2

    @staticmethod
    def _strip_operation_words(query: str) -> str:
        """去掉问题中的操作类关键词，剩下的内容用于匹配列名"""
        import re

        q = query
        # 常见操作/疑问词（按长度从长到短排，避免短词先匹配）
        op_words = [
            "销售金额",
            "总销售额",
            "总金额",
            "有多少个",
            "有多少",
            "多少个",
            "几名",
            "几个",
            "一共多少",
            "总共",
            "总额",
            "合计",
            "共计",
            "总和",
            "平均",
            "哪些",
            "有哪些",
            "排名",
            "最高",
            "最多",
            "前三",
            "前几名",
            "分别是",
            "信息",
            "详情",
            "详细",
            "每个",
            "分别",
            "是什么",
            "有什么",
            "在哪里",
            "是哪",
            "多少",
            "怎么",
            "为什么",
            "谁的",
            "哪个",
            "什么",
            "是",
            "有",
            "在",
            "的",
            "吗",
            "呢",
            "啊",
            "吧",
        ]
        for w in op_words:
            q = q.replace(w, "")
        # 去掉标点和多余空格
        q = re.sub(r"[，。？、,./?！!;；:：\s]+", " ", q).strip()
        return q

    @staticmethod
    def _match_best_column(query_remainder: str, columns: list) -> tuple:
        """
        从剩余问题文本中匹配最可能的目标列。
        返回 (ColumnInfo, score)，没匹配上返回 (None, 0.0)。
        """
        if not query_remainder:
            return None, 0.0

        best_col = None
        best_score = 0.0

        q = query_remainder.lower()

        for col in columns:
            score = 0.0
            col_name = col.name.lower()

            # 精确包含列名 → 高分
            if col_name in q:
                score = max(score, 0.9)

            # 别名包含
            for alias in col.aliases:
                if alias.lower() in q:
                    score = max(score, 0.85)

            # 样本值包含（仅在字符串列、且值在问题中时小幅加分）
            if col.semantic_type in ("person", "organization", "location", "category", "other"):
                for sv in col.sample_values[:5]:
                    if sv and sv.lower() in q:
                        score = max(score, 0.5)
                        break

            if score > best_score:
                best_score = score
                best_col = col

        return best_col, best_score

    def _extract_entity_candidates(self, query: str, columns: list) -> list[tuple[str, str, float]]:
        """
        从问题中提取实体候选（列名 → 值）。

        通过将各列的样本值与问题做子串匹配来找到过滤条件。
        返回 [(col_name, value, score), ...]。
        """
        results = []
        q = query

        for col in columns:
            # 只对语义上可能作为过滤条件的列做匹配
            if col.semantic_type not in (
                "person",
                "organization",
                "location",
                "category",
                "time",
                "id",
                "other",
            ):
                continue

            for sv in col.sample_values[:10]:
                if not sv or len(sv) < 2:
                    continue
                if sv in q:
                    # 置信度：完全匹配 0.9，根据列语义类型微调
                    score = 0.9
                    if col.semantic_type == "other":
                        score = 0.7
                    results.append((col.name, sv, score))
                    break  # 每列最多一个匹配值

        return results

    @staticmethod
    def _merge_aggregate_hits(hits: list) -> list:
        """
        多表聚合并：将多个表的同类型聚合结果合并为一个总聚合结果。

        合并规则：
        - SUM：直接相加
        - COUNT：直接相加
        - AVG：加权平均（按各表的记录数加权）
        - 不同目标列的聚合不合并（比如一个表是"销售金额"，一个表是"数量"，各算各的）

        合并后的聚合 hit 放在列表最前面，各表的明细仍然保留。
        """
        from .types import RetrievalHit, RetrievalSource

        # 按 (operation, target_column) 分组
        agg_groups: dict[tuple, list] = {}
        non_agg_hits: list = []

        for h in hits:
            if h.payload and h.payload.get("type") == "aggregate":
                op = h.payload.get("operation", "")
                target = h.payload.get("target_column", "")
                key = (op, target)
                if key not in agg_groups:
                    agg_groups[key] = []
                agg_groups[key].append(h)
            else:
                non_agg_hits.append(h)

        # 构建结果：合并后的聚合 + 各表的原始聚合（标记 per_table）+ 明细
        result_hits = []

        for (op, target), group in agg_groups.items():
            if len(group) <= 1:
                # 只有1个聚合，直接用原 hit
                result_hits.extend(group)
                continue

            # 多个表的同类型同列聚合 → 合并
            values = []
            counts = []
            units = set()
            target_aliases = set()

            for h in group:
                val = h.payload.get("value", 0)
                cnt = h.payload.get("total_count", 0)
                try:
                    values.append(float(val))
                except (ValueError, TypeError):
                    values.append(0)
                try:
                    counts.append(int(cnt))
                except (ValueError, TypeError):
                    counts.append(0)
                if h.payload.get("unit"):
                    units.add(h.payload.get("unit"))
                if h.payload.get("target_column_alias"):
                    target_aliases.add(h.payload.get("target_column_alias"))
                # 标记原始聚合为 per_table（用于展示来源）
                h.metadata = h.metadata or {}
                h.metadata["per_table_aggregate"] = True

            # 计算合并值
            if op in ("sum", "count", "count_distinct"):
                merged_value = sum(values)
                if op == "count":
                    merged_display = (
                        int(merged_value) if merged_value == int(merged_value) else merged_value
                    )
                else:
                    merged_display = (
                        round(merged_value, 2) if isinstance(merged_value, float) else merged_value
                    )
            elif op == "avg":
                # 加权平均
                total_count = sum(counts)
                if total_count > 0:
                    weighted_sum = sum(v * c for v, c in zip(values, counts))
                    merged_value = weighted_sum / total_count
                    merged_display = round(merged_value, 2)
                else:
                    merged_value = 0
                    merged_display = 0
            else:
                # 其他操作不合并
                result_hits.extend(group)
                continue

            total_count = sum(counts)
            unit = list(units)[0] if len(units) == 1 else ""
            target_alias = (
                list(target_aliases)[0] if len(target_aliases) == 1 else (target or "结果")
            )
            op_label = "合计" if op == "sum" else ("平均值" if op == "avg" else "总数")

            merged_hit = RetrievalHit(
                source=RetrievalSource.SQL,
                score=1.0,
                content=f"{target_alias}{op_label}（共{len(group)}个数据源）：{merged_display}{unit}",
                payload={
                    "type": "aggregate",
                    "operation": op,
                    "target_column": target,
                    "target_column_alias": target_alias,
                    "value": merged_display,
                    "is_merged": True,
                    "source_table_count": len(group),
                    "total_count": total_count,
                    "unit": unit,
                    "filters": group[0].payload.get("filters", []) if group else [],
                },
                metadata={"merged": True, "source_tables": len(group)},
            )
            # 合并后的聚合放最前面
            result_hits.append(merged_hit)
            # 然后是各表的原始聚合（保留来源信息）
            result_hits.extend(group)

        # 最后是非聚合 hits（明细、列表等）
        result_hits.extend(non_agg_hits)
        return result_hits

    @staticmethod
    def _merge_group_agg_hits(hits: list, limit: int = 0) -> list:
        """跨表合并分组聚合结果（GROUP_AGG）

        多张销售表各自返回"按业务员分组的销售额"时，同一业务员需要跨表求和，
        再在全局重新排序，得到真正的全局排名。最后按 limit 截断（0=不截断）。

        合并规则（按 group_column + metric_column + agg_func 分组）：
        - sum / count：跨表求和
        - max / min：取极值
        - avg：跨表按均值（近似：各组均值再平均）
        排序方向沿用 payload.order_dir（默认 DESC），并重标 rank。
        """
        from .types import RetrievalHit, RetrievalSource

        group_hits = [h for h in hits if h.payload and h.payload.get("type") == "group_agg"]
        if not group_hits:
            return hits
        other_hits = [h for h in hits if not (h.payload and h.payload.get("type") == "group_agg")]

        def _num(h) -> float:
            try:
                return float(h.payload.get("value", 0))
            except (TypeError, ValueError):
                return 0.0

        buckets: dict[tuple, dict] = {}
        order_dir = "DESC"
        for h in group_hits:
            p = h.payload or {}
            if p.get("empty"):
                continue
            order_dir = (p.get("order_dir") or order_dir).upper()
            key = (
                p.get("group_column", ""),
                p.get("metric_column", ""),
                p.get("agg_func", "sum"),
            )
            gval = p.get("group_value")
            if gval is None or gval == "":
                continue
            agg = p.get("agg_func", "sum")
            bucket = buckets.setdefault(key, {})
            if gval not in bucket:
                bucket[gval] = {"value": _num(h), "count": 1, "hit": h}
            else:
                cur = bucket[gval]
                if agg == "max":
                    cur["value"] = max(cur["value"], _num(h))
                elif agg == "min":
                    cur["value"] = min(cur["value"], _num(h))
                else:  # sum / count / avg（avg 先累加，最后除以份数）
                    cur["value"] += _num(h)
                cur["count"] += 1

        merged: list = []
        reverse = order_dir != "ASC"
        for (gcol, mcol, agg), bucket in buckets.items():
            items = []
            for gval, info in bucket.items():
                v = info["value"]
                if agg == "avg" and info["count"] > 0:
                    v = v / info["count"]
                items.append((gval, v, info["hit"]))
            items.sort(key=lambda x: x[1], reverse=reverse)
            if limit and limit > 0:
                items = items[:limit]

            for i, (gval, v, sample) in enumerate(items):
                fmt = int(v) if float(v) == int(v) else round(v, 2)
                hit = RetrievalHit(
                    source=RetrievalSource.SQL,
                    score=1.0,
                    content=f"{gcol}：{gval}，{mcol or '记录数'}：{fmt}",
                    payload={
                        "type": "group_agg",
                        "group_column": gcol,
                        "group_value": gval,
                        "metric_column": mcol,
                        "metric_column_alias": mcol,
                        "agg_func": agg,
                        "order_dir": order_dir,
                        "value": fmt,
                        "rank": i + 1,
                        "filters": (sample.payload or {}).get("filters", []),
                        "is_merged": True,
                        "source_table_count": len(bucket),
                    },
                    metadata={"merged": True, "rank": i + 1},
                )
                if i == 0:
                    hit.payload["_total_groups"] = len(items)
                    hit.payload["_shown_groups"] = len(items)
                merged.append(hit)

        return merged + other_hits

    # ------------------------------------------------------------------
    # 表格文件查找与排序
    # ------------------------------------------------------------------

    @staticmethod
    def _preprocess_query(query: str) -> str:
        """
        用户 query 入口统一预处理

        处理范围（保守清洗，不改变语义）：
        1. 零宽字符 / BOM / 不可见控制字符
        2. 全角空格 → 半角空格
        3. 首尾空白（空格 / tab / 换行 / 回车）
        4. 中间连续空白 → 单个空格
        5. 首尾常见装饰符（#、👉、📌、问：、Q: 等）

        不做的事（避免影响语义识别）：
        - 不转小写（中文大小写不敏感，英文实体名可能需要大小写）
        - 不去标点（标点可能是实体的一部分，如"C++"）
        - 不全角转半角（中文全角标点是正常的）
        """
        if not query:
            return ""

        text = query

        # 1. 零宽字符 / BOM / 常见不可见字符
        zero_width = [
            "\ufeff",  # BOM
            "\u200b",  # zero-width space
            "\u200c",  # zero-width non-joiner
            "\u200d",  # zero-width joiner
            "\u2060",  # word joiner
            "\u00ad",  # soft hyphen
            "\u034f",  # combining grapheme joiner
            "\u180e",  # mongolian vowel separator
        ]
        for ch in zero_width:
            text = text.replace(ch, "")

        # 2. 全角空格 → 半角空格
        text = text.replace("\u3000", " ")

        # 3. 控制字符（除了常见空白，其他都去掉）
        # 保留 \t \n \r 在合并空格时处理
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)

        # 4. 合并连续空白（空格 / tab / 换行 / 全角空格等都合并为单个半角空格）
        text = re.sub(r"\s+", " ", text)

        # 5. 去首尾空白
        text = text.strip()

        if not text:
            return ""

        # 6. 首尾常见装饰符 / 引导词（只剥一层，避免过度清洗）
        #    例如："# 问题：xxx"、"👉 xxx"、"问: xxx"、"Q: xxx"
        for pat in QUERY_PREFIX_PATTERNS:
            new_text = re.sub(pat, "", text)
            if new_text != text and len(new_text) >= 2:
                text = new_text
                break  # 只剥一层

        # 再去一次首尾空白
        text = text.strip()

        return text if text else query

    def _rank_table_files(
        self,
        table_files: list[dict],
        query: str,
        table_schemas: list | None = None,
    ) -> list[dict]:
        """
        根据查询与表格的相关性排序。

        排序策略（多级加权）：
        1. 文件名关键词匹配（权重高）
        2. 列名关键词匹配（权重中，需要传入 schemas）
        3. 实体类型匹配（如人名、部门、金额等语义匹配）
        4. 原始顺序（保底，保证稳定性）

        Args:
            table_files: 表格文件列表
            query: 查询语句
            table_schemas: 可选，表格 schema 列表（与 table_files 一一对应），
                           用于做列名级别的语义匹配
        """
        if len(table_files) <= 1:
            return table_files

        if extract_profile_subject(query) and not table_schemas:
            return table_files

        # A person's profile belongs to a roster, even if transaction tables
        # contain the same name in a salesperson column. Use schema evidence
        # because uploaded files often have generic names.
        if (
            table_schemas
            and extract_profile_subject(query)
        ):
            profile_files = []
            for file_info, schema in zip(table_files, table_schemas):
                columns = {col.name for col in schema.columns} if schema else set()
                if (
                    columns & {"姓名", "员工姓名", "员工名称", "名字"}
                    and len(columns & {"员工编号", "工号", "性别", "部门", "职位", "入职日期", "出生日期", "年龄", "手机", "手机号", "邮箱"}) >= 2
                ):
                    profile_files.append(file_info)
            if profile_files:
                return profile_files

        # ---- 提取查询关键词 ----
        query_keywords = set()
        for match in re.finditer(CHINESE_KEYWORD_RE, query):
            keyword = match.group()
            if keyword not in QUERY_STOPWORDS:
                query_keywords.add(keyword)

        if not query_keywords:
            return table_files

        # ---- 语义实体识别（用于提升特定类型表的匹配度） ----
        # 配置在 constants.ENTITY_TABLE_BOOSTS，新增业务类型时直接加配置即可
        entity_matches: list[dict] = []
        for boost_cfg in ENTITY_TABLE_BOOSTS:
            if re.search(boost_cfg["query_pattern"], query):
                entity_matches.append(boost_cfg)

        # ---- 打分 ----
        scored = []
        for i, f in enumerate(table_files):
            file_name = f.get("file_name", "")
            score = 0.0

            # 1. 文件名关键词匹配
            name_match_score = sum(len(kw) for kw in query_keywords if kw in file_name)
            score += name_match_score * TABLE_RANK_WEIGHTS["file_name_match"]

            # 2. 列名关键词匹配 — 需要 schema
            if table_schemas and i < len(table_schemas):
                schema = table_schemas[i]
                if schema and hasattr(schema, "columns"):
                    col_names = " ".join(
                        [col.name if hasattr(col, "name") else str(col) for col in schema.columns]
                    )
                    col_match_score = sum(len(kw) for kw in query_keywords if kw in col_names)
                    score += col_match_score * TABLE_RANK_WEIGHTS["column_name_match"]

            # 3. 语义实体类型匹配（强信号）
            #    根据查询中提到的实体类型，提升对应类型表格的排名
            for boost_cfg in entity_matches:
                if any(kw in file_name for kw in boost_cfg["table_keywords"]):
                    score += boost_cfg["bonus"]

            # 4. 原始顺序（保底权重，保证稳定性）
            scored.append((score, -i * TABLE_RANK_WEIGHTS["original_order"], f))

        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)

        # 如果最高分仍然是 0，说明没有任何匹配，保持原顺序
        if scored[0][0] <= 0:
            return table_files

        # 存在正相关表时，丢弃与查询"零相关"的表：
        # 避免不相关表（如"员工信息"）的明细进入多表合并，污染统计结果与答案展示
        ranked = [f for score, _, f in scored if score > 0]
        if ranked:
            dropped = [f.get("file_name", "") for score, _, f in scored if score <= 0]
            if dropped:
                logger.debug(f"Table ranking dropped irrelevant tables: {dropped}")
            return ranked
        return [f for _, _, f in scored]

    async def _find_table_files(self, collection_id: str) -> list[dict]:
        """查找可用于查询的表格文件（Excel/CSV）

        Args:
            collection_id: 知识库ID，传 "all" 或空字符串表示查询所有知识库
        """
        try:
            from sqlalchemy import select

            from app.models.db.collection import CollectionDB

            is_cross_collection = not collection_id or collection_id == "all"

            if is_cross_collection:
                # 跨库查询：查找可见范围内所有启用的知识库中的表格文件（SaaS 隔离）
                stmt = (
                    select(DocumentDB, DocumentVersionDB, CollectionDB)
                    .join(DocumentVersionDB, DocumentDB.current_version_id == DocumentVersionDB.id)
                    .join(CollectionDB, DocumentDB.collection_id == CollectionDB.id)
                    .where(
                        DocumentDB.file_type.in_(["xlsx", "xls", "csv"]),
                        DocumentVersionDB.status == "completed",
                        CollectionDB.is_active == True,
                    )
                    .order_by(DocumentDB.updated_at.desc())
                )
                if self.visible_ids is not None:
                    stmt = stmt.where(CollectionDB.owner_user_id.in_(self.visible_ids))
                result = await self.db.execute(stmt)
                rows = result.all()

                table_files = []
                for doc, version, coll in rows:
                    local_path = self.storage.get_local_path(version.storage_path)
                    if local_path:
                        import os

                        if os.path.exists(local_path):
                            table_files.append(
                                {
                                    "file_path": local_path,
                                    "file_name": doc.name,
                                    "document_id": doc.id,
                                    "version_id": version.id,
                                    "sheet_name": "",
                                    "collection_id": coll.id,
                                    "collection_name": coll.name,
                                }
                            )
            else:
                # 单知识库查询
                result = await self.db.execute(
                    select(DocumentDB, DocumentVersionDB)
                    .join(DocumentVersionDB, DocumentDB.current_version_id == DocumentVersionDB.id)
                    .where(
                        DocumentDB.collection_id == collection_id,
                        DocumentDB.file_type.in_(["xlsx", "xls", "csv"]),
                        DocumentVersionDB.status == "completed",
                    )
                    .order_by(DocumentDB.updated_at.desc())
                )
                rows = result.all()

                table_files = []
                for doc, version in rows:
                    local_path = self.storage.get_local_path(version.storage_path)
                    if local_path:
                        import os

                        if os.path.exists(local_path):
                            table_files.append(
                                {
                                    "file_path": local_path,
                                    "file_name": doc.name,
                                    "document_id": doc.id,
                                    "version_id": version.id,
                                    "sheet_name": "",
                                    "collection_id": collection_id,
                                }
                            )

            return table_files

        except Exception as e:
            logger.warning(f"Failed to find table files: {e}")
            return []

    # ------------------------------------------------------------------
    # 资源管理
    # ------------------------------------------------------------------

    def close(self) -> None:
        """关闭资源"""
        self.schema_provider.clear_cache()
