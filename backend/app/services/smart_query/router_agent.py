"""
RouterAgent - 路由代理

负责：
1. 意图分类：判断是结构化查询 / 语义查询 / 混合查询 / 直接回答
2. 实体解析：识别用户问题中的业务实体及其所属字段
3. 查询计划生成：生成结构化的查询计划（QueryPlan）

设计原则：
- LLM + 规则混合判断，提高准确率
- 支持"手下/下属/负责"等隐含层级关系
- 输出严格 JSON 格式，便于下游解析
- 失败时降级为语义查询，保证可用性
"""

from __future__ import annotations

import copy
import json
import re
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.llm.factory import create_llm
from app.services.query_state import enforce_removed_columns, extract_profile_subject
from .constants import DEFAULT_MAX_RETRIES, PLAN_CACHE_MAX, get_llm_config
from .prompts import get_prompt
from .types import (
    EntityInfo,
    FilterInfo,
    QueryIntent,
    QueryOperation,
    QueryPlan,
    TableSchema,
)
from .schema_provider import SchemaProvider

logger = get_logger(__name__)

# 规则匹配关键词（用于快速判断，减少 LLM 调用）
STRUCTURED_KEYWORDS_PATTERNS = [
    # 统计类
    (r"多少|几个|数量|总数|总计|合计|统计|共多少|总共有|有多少", QueryOperation.COUNT),
    # 列表类（"分别是谁/分别是"本质是去重列表，不是分组统计）
    (r"有哪些|哪些|列出|列表|名单|明细|分别是|都是谁|都有谁|包括哪些|包含哪些|分别是谁|分别是哪些", QueryOperation.LIST),
    # 分组统计（必须有"按...分组/统计"或"每个X有多少"这样明确的分组统计意图）
    (r"每个.*多少|每个.*有|各个.*多少|按.*分组|按.*统计|按.*计算|分组统计|分别统计|分别计算", QueryOperation.GROUP_COUNT),
    # Top N
    (r"top\s*\d+|前\d+|排名|最高|最多|最大|第一名|前三|前十", QueryOperation.TOP_N),
    # 求和（"金额/总额/销售额"类问法本质是求和）
    (r"总和|合计|共计|总计金额|总金额|销售额|销售总额|总额|金额", QueryOperation.SUM),
    # 平均值
    (r"平均|均值|平均值", QueryOperation.AVG),
    # 详情查询（注意："分别是谁/都有谁/都是谁"属于列表类，不在这里匹配）
    # "X的信息" 模式：后面不跟"多少/几个"，避免与统计类混淆
    (r"(?<!分别)(?<!都)(?<!各)(?<!还)是谁|(?<!分别)(?<!都)(?<!各)(?<!还)是什么|详细信息|详情|介绍.*下|介绍一下|基本信息|个人信息|全部信息|的信息(?![多少几个])", QueryOperation.DETAIL),
    # 是否存在
    (r"有没有|是否有|存在不存在|在不在", QueryOperation.EXISTS),
]

# 层级关系关键词（"手下"、"下属"、"下面"、"负责的"等）
HIERARCHY_RELATION_KEYWORDS = [
    "手下", "下属", "下面", "底下", "负责的", "管的", "带的",
    "属于", "归", "在...下面", "在...手下", "的人", "的员工",
    "的业务员", "的销售", "的人员",
]

# 数值型列类型（用于识别 GROUP_AGG 的度量列）
NUMERIC_TYPES = ("INTEGER", "BIGINT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")

# 分组聚合（GROUP_AGG）触发词：排名 / 极值
GROUP_RANK_PATTERN = re.compile(
    r"最多|最高|最大|最少|最低|最小|排名|排行|第一|榜首",
    re.IGNORECASE,
)
# "前N / Top N"（N 支持阿拉伯数字与常见中文数字）
GROUP_LIMIT_PATTERN = re.compile(r"前\s*([0-9一二三四五六七八九十两]+)|top\s*([0-9]+)", re.IGNORECASE)
# 单值提问：哪个 / 哪一位 / 谁
GROUP_SINGLE_HINT = re.compile(r"哪个|哪一个|哪位|哪一位|哪家|哪名|谁")
# 升序（最少 / 最低 / 最差）
GROUP_ASC_PATTERN = re.compile(r"最少|最低|最小|最差")
# 分组统计词（无排名词时凭此触发，且必须同时命中数值度量列，避免误伤列表类问题）
GROUP_STATS_PATTERN = re.compile(
    r"按.{0,12}(分组|统计|汇总|合计|求和)|分组统计|每个|各个|每名|每位|分别.{0,8}(统计|汇总|合计)"
)

# 结构化查询置信度阈值（高于此值直接走 SQL，低于则走 LLM 确认）
RULE_BASED_CONFIDENCE_THRESHOLD = 0.7


class RouterAgent:
    """
    路由代理

    接收用户问题，输出查询计划 QueryPlan，决定后续走哪条路径。
    """

    def __init__(self, schema_provider: Optional[SchemaProvider] = None):
        self.schema_provider = schema_provider or SchemaProvider()
        self._llm = None
        # 查询计划缓存（LLM 规划较贵，相同问句 + 相同表结构可复用）
        self._plan_cache: dict[tuple, QueryPlan] = {}
        self._plan_cache_max = PLAN_CACHE_MAX

    def _get_llm(self):
        """获取 LLM 实例（懒加载）"""
        if self._llm is None:
            try:
                llm_cfg = get_llm_config("router", settings)
                self._llm = create_llm(
                    provider=llm_cfg["provider"],
                    model=llm_cfg["model"],
                )
            except Exception as e:
                logger.warning(f"Failed to create LLM for RouterAgent: {e}")
                self._llm = None
        return self._llm

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def analyze(
        self,
        query: str,
        file_path: str,
        sheet_name: str = "",
        schema: Optional[TableSchema] = None,
        inherited_state: Optional[dict] = None,
    ) -> QueryPlan:
        """
        分析用户问题，生成查询计划

        Args:
            query: 用户问题
            file_path: Excel/CSV 文件路径
            sheet_name: Sheet 名
            schema: 可选，预加载的表结构
            inherited_state: 可选，会话结构化状态（多轮范围/维度继承），形如
                {"filters": [...], "group_by": "...", "metric": "...", "order_dir": "...", "limit": 3}

        Returns:
            QueryPlan 查询计划
        """
        if schema is None:
            try:
                schema = self.schema_provider.get_schema(file_path, sheet_name)
            except Exception as e:
                logger.warning(f"Failed to load schema, falling back to semantic: {e}")
                return QueryPlan(
                    intent=QueryIntent.SEMANTIC,
                    operation=QueryOperation.UNKNOWN,
                    reasoning=f"无法加载表结构，降级为语义检索: {e}",
                    needs_vector=True,
                    confidence=0.5,
                )

        # 计划缓存命中：相同问句 + 相同表结构 + 相同继承范围，直接复用（省一次 LLM 规划）
        cached_plan = self._get_cached_plan(query, schema, inherited_state)
        if cached_plan is not None:
            logger.debug(f"Query plan cache hit: {query[:40]}")
            return cached_plan

        # Step 1: 规则快速匹配（低成本、高确定性）
        rule_based_plan = self._rule_based_analyze(query, schema)

        # Step 2: 实体解析（所有路径都执行，不依赖 LLM）
        entities: list[EntityInfo] = []
        try:
            entities = self._resolve_entities(query, file_path, sheet_name, schema)
        except Exception as e:
            logger.debug(f"Entity resolution failed: {e}")

        # 规则计划做确定性后处理（实体 / 层级 / 目标列 / 数值聚合 / 分组聚合 / 纠偏）
        rule_based_plan = self._postprocess_plan(
            query, schema, rule_based_plan, entities, inherited_state
        )

        llm_first = getattr(settings, "smart_query_llm_first", True)

        if not llm_first and rule_based_plan.confidence >= RULE_BASED_CONFIDENCE_THRESHOLD:
            # 规则优先模式（可选回退）：高置信时直接用规则结果
            rule_based_plan.suggested_top_k = self._calc_suggested_top_k(rule_based_plan)
            logger.debug(
                f"Rule-based intent detection: {rule_based_plan.intent}, "
                f"op={rule_based_plan.operation}, "
                f"confidence={rule_based_plan.confidence:.2f}, "
                f"entities={[e.name for e in rule_based_plan.entities]}, "
                f"target={rule_based_plan.target_column}, "
                f"suggested_top_k={rule_based_plan.suggested_top_k}"
            )
            self._set_cached_plan(query, schema, rule_based_plan, inherited_state)
            return rule_based_plan

        # Step 4: LLM 深度分析
        # - LLM 主导（默认）：无论规则置信度如何，都以 LLM 计划为准，规则仅做校验/兜底
        # - 规则优先（开关关闭）：低置信时才问 LLM，并融合两者
        llm_plan = await self._llm_based_analyze(query, schema)

        if llm_plan is None:
            # LLM 不可用 / 输出无效 → 回退规则计划（护栏）
            logger.warning("LLM planning unavailable/invalid, fallback to rule-based plan")
            final_plan = rule_based_plan
        elif llm_first:
            # 规则与 LLM 判定一致 → 提升置信度
            if (
                llm_plan.intent == rule_based_plan.intent
                and llm_plan.operation == rule_based_plan.operation
                and llm_plan.confidence < 0.98
            ):
                llm_plan.confidence = min(0.98, llm_plan.confidence + 0.1)
            # 以 LLM 计划为基准，再做确定性后处理（实体/层级/目标列/分组聚合/纠偏）
            final_plan = self._postprocess_plan(
                query, schema, llm_plan, entities, inherited_state
            )
            # 规则阶段识别出的"属性投影"在 LLM 计划中可能缺失，补回
            if not final_plan.projection_column and rule_based_plan.projection_column:
                final_plan.projection_column = rule_based_plan.projection_column
        else:
            # 规则优先模式的低置信分支：融合规则与 LLM 结果
            final_plan = self._merge_plans(rule_based_plan, llm_plan)
            final_plan = self._postprocess_plan(
                query, schema, final_plan, entities or rule_based_plan.entities,
                inherited_state,
            )
            if not final_plan.projection_column and rule_based_plan.projection_column:
                final_plan.projection_column = rule_based_plan.projection_column

        final_plan.suggested_top_k = self._calc_suggested_top_k(final_plan)

        logger.info(
            f"Query analysis completed (llm_first={llm_first}): "
            f"intent={final_plan.intent}, op={final_plan.operation}, "
            f"target={final_plan.target_column}, group_by={final_plan.group_by}, "
            f"metric={final_plan.metric_column}, "
            f"filters={[(f.column, f.value) for f in final_plan.filters]}, "
            f"confidence={final_plan.confidence:.2f}, "
            f"suggested_top_k={final_plan.suggested_top_k}"
        )

        self._set_cached_plan(query, schema, final_plan, inherited_state)
        return final_plan

    # ------------------------------------------------------------------
    # 查询计划缓存
    # ------------------------------------------------------------------

    def _plan_cache_key(
        self, query: str, schema: TableSchema, inherited_state: Optional[dict] = None
    ) -> tuple:
        cols = tuple(c.name for c in schema.columns)
        scope_filters = self._scope_filters(inherited_state)
        scope = tuple(
            sorted(
                (str(f.get("column", "")), str(f.get("value", "")))
                for f in scope_filters
                if isinstance(f, dict)
            )
        )
        group_by = ""
        if isinstance(inherited_state, dict):
            group_by = str(inherited_state.get("group_by") or "")
        return (
            (query or "").strip(),
            schema.file_path or schema.table_name,
            schema.sheet_name,
            schema.row_count,
            cols,
            scope,
            group_by,
        )

    def _get_cached_plan(
        self, query: str, schema: TableSchema, inherited_state: Optional[dict] = None
    ) -> Optional[QueryPlan]:
        if not getattr(settings, "smart_query_plan_cache_enabled", True):
            return None
        plan = self._plan_cache.get(self._plan_cache_key(query, schema, inherited_state))
        return copy.deepcopy(plan) if plan is not None else None

    def _set_cached_plan(
        self,
        query: str,
        schema: TableSchema,
        plan: QueryPlan,
        inherited_state: Optional[dict] = None,
    ) -> None:
        if not getattr(settings, "smart_query_plan_cache_enabled", True):
            return
        self._plan_cache[self._plan_cache_key(query, schema, inherited_state)] = copy.deepcopy(plan)
        if len(self._plan_cache) > self._plan_cache_max:
            # FIFO 淘汰最旧一条
            self._plan_cache.pop(next(iter(self._plan_cache)), None)

    # ------------------------------------------------------------------
    # 确定性后处理（规则 / LLM 计划共用，作为"护栏"）
    # ------------------------------------------------------------------

    def _postprocess_plan(
        self,
        query: str,
        schema: TableSchema,
        plan: QueryPlan,
        entities: Optional[list[EntityInfo]] = None,
        inherited_state: Optional[dict] = None,
    ) -> QueryPlan:
        """对查询计划做确定性后处理（不改动 LLM 的结构化意图，只做规整与纠偏）

        顺序：
        1. 挂载实体（供层级过滤推断使用）
        2. 继承会话范围（结构化 filters，当前查询未指定同列时补上）
        3. 目标列匹配（非 DETAIL/EXISTS 且未指定时）
        4. 层级关系推断（实体 → 过滤条件）
        5. SUM/AVG 数值目标列推断
        6. 目标列后处理（DETAIL/EXISTS/LIST 场景修正）
        7. 分组聚合升级（GROUP_AGG，含"维度槽位"继承）/ TOP_N 纠偏
        """
        if entities:
            plan.entities = entities

        # 多轮范围继承：仅当当前计划未涉及该列时补上（当前查询显式同列条件优先/覆盖）
        scope_filters = self._scope_filters(inherited_state)
        if scope_filters:
            self._merge_scope_filters(plan, [
                item for item in scope_filters
                if isinstance(item, dict) and schema.get_column(str(item.get("column") or ""))
            ])

        # 目标列（DETAIL 查整行详情、EXISTS 判断存在性，均不需要目标列）
        if not plan.target_column and plan.operation not in (
            QueryOperation.DETAIL,
            QueryOperation.EXISTS,
        ):
            matched_target = self._match_target_column(query, schema)
            if matched_target:
                plan.target_column = matched_target.name
                plan.target_column_alias = matched_target.name

        self._infer_hierarchy_relation(plan, schema)

        subject = extract_profile_subject(query)
        if subject and any(f.value == subject and f.source != "inherited" for f in plan.filters):
            plan.intent = QueryIntent.STRUCTURED
            plan.operation = QueryOperation.DETAIL
            plan.needs_sql = True
            plan.confidence = max(plan.confidence, 0.8)

        if plan.operation in (QueryOperation.SUM, QueryOperation.AVG):
            self._infer_numeric_target_for_agg(plan, schema, query)

        self._finalize_target_column(plan, schema)

        # 分组聚合升级（当前问题未给出维度时，可继承上一轮的分析对象/度量/条数）
        grouped_plan = self._maybe_group_agg(query, schema, plan, inherited_state)
        if grouped_plan is not None:
            plan = grouped_plan
        else:
            self._correct_top_n_target(query, schema, plan)

        # 校验（护栏）：被显式移除的槽位不得在最终查询条件中重现
        forbidden = ()
        if isinstance(inherited_state, dict):
            forbidden = tuple(inherited_state.get("forbidden_columns") or ())
        if forbidden:
            kept, dropped = enforce_removed_columns(plan.filters, forbidden)
            if dropped:
                logger.info(
                    "Forbidden filters stripped (explicitly removed slot): "
                    f"{[getattr(f, 'column', '') for f in dropped]}"
                )
                plan.filters = kept

        return plan

    @staticmethod
    def _scope_filters(inherited_state: Optional[dict]) -> list[dict]:
        """从会话状态中取出过滤条件（兼容旧的 list 形式）"""
        if not inherited_state:
            return []
        if isinstance(inherited_state, dict):
            filters = inherited_state.get("filters")
            return filters if isinstance(filters, list) else []
        if isinstance(inherited_state, list):
            return inherited_state
        return []

    @staticmethod
    def _merge_scope_filters(plan: QueryPlan, scope_filters: list[dict]) -> None:
        """把会话继承的过滤条件合并进计划（同列以当前计划为准，携带来源元数据）"""
        existing_cols = {f.column for f in plan.filters}
        for item in scope_filters or []:
            if not isinstance(item, dict):
                continue
            col = str(item.get("column") or "").strip()
            val = item.get("value")
            if not col or val in (None, ""):
                continue
            if col in existing_cols:
                # 当前查询已显式指定该列（如换了一个客户）→ 覆盖，不继承
                continue
            try:
                turn = int(item.get("turn") or 0)
            except (TypeError, ValueError):
                turn = 0
            try:
                confidence = float(item.get("confidence") or 1.0)
            except (TypeError, ValueError):
                confidence = 1.0
            plan.filters.append(FilterInfo(
                column=col,
                column_alias=str(item.get("column_alias") or col),
                operator=str(item.get("operator") or "="),
                value=str(val),
                entity_name=str(val),
                # 合并进本轮计划 → 来源标记为"历史继承"，保留其最初产生的轮次
                source="inherited",
                turn=turn,
                explicit=False,
                confidence=confidence,
            ))
            existing_cols.add(col)

    def _infer_numeric_target_for_agg(
        self, plan: QueryPlan, schema: TableSchema, query: str
    ) -> None:
        """SUM/AVG 的目标列必须是数值列

        若当前目标列不是数值列（如被误匹配到维度列"销售经理"）：
        - 用数值度量列替换目标列
        - 原维度目标列 + 实体 → 转为过滤条件
        """
        current_target = plan.target_column
        current_is_numeric = False
        if current_target:
            col = schema.get_column(current_target)
            if col and col.data_type in NUMERIC_TYPES:
                current_is_numeric = True

        if current_is_numeric:
            return

        numeric_target = self._match_numeric_target_column(query, schema, plan.operation)
        if not numeric_target:
            return

        if current_target and plan.entities:
            for ent in plan.entities:
                if ent.column_name == current_target and ent.name:
                    existing = any(
                        f.column == current_target and f.value == ent.name
                        for f in plan.filters
                    )
                    if not existing:
                        plan.filters.append(FilterInfo(
                            column=current_target,
                            column_alias=ent.column_alias or current_target,
                            value=ent.name,
                            entity_name=ent.name,
                        ))

        plan.target_column = numeric_target.name
        plan.target_column_alias = numeric_target.name
        if plan.confidence < 0.95:
            plan.confidence = min(0.95, plan.confidence + 0.08)

    # ------------------------------------------------------------------
    # 规则匹配
    # ------------------------------------------------------------------

    def _rule_based_analyze(self, query: str, schema: TableSchema) -> QueryPlan:
        """基于规则的快速分析"""
        query_lower = query.lower().strip()
        confidence = 0.0
        operation = QueryOperation.UNKNOWN
        matched_pattern = ""

        # 匹配操作类型：遍历所有模式，取置信度最高的
        # 置信度计算原则：
        #   1. 模式越具体（关键词越多、越独特），置信度越高
        #   2. GROUP_COUNT 比 COUNT 更具体（"每个...有多少" vs "有多少"）
        #   3. 匹配到的关键词数量越多，置信度越高
        best_confidence = 0.0
        best_operation = QueryOperation.UNKNOWN
        best_pattern = ""

        for pattern, op in STRUCTURED_KEYWORDS_PATTERNS:
            matches = re.findall(pattern, query_lower)
            if matches:
                # 计算模式特异性得分
                # 基础分按操作类型的特异性排序
                base_confidence = {
                    "group_count": 0.75,   # 最具体：每个...有多少
                    "top_n": 0.7,          # 较具体：前N名、排名
                    "sum": 0.68,           # 较具体：总和、合计
                    "avg": 0.68,           # 较具体：平均
                    "detail": 0.65,        # 较具体：详情、明细
                    "exists": 0.6,         # 一般：有没有、是否有
                    "count": 0.55,         # 最泛：多少、数量
                    "list": 0.5,           # 最泛：有哪些、列出
                }.get(op.value, 0.5)

                # 匹配次数加成（匹配到越多关键词越高，但上限 +0.1）
                match_count = len(matches) if isinstance(matches, list) else 1
                match_bonus = min(0.1, match_count * 0.03)

                total_confidence = base_confidence + match_bonus

                if total_confidence > best_confidence:
                    best_confidence = total_confidence
                    best_operation = op
                    best_pattern = pattern

        if best_operation != QueryOperation.UNKNOWN:
            operation = best_operation
            confidence = best_confidence
            matched_pattern = best_pattern

        # ---- 冲突修正：列举意图优先 ----
        # "分别是谁/都有谁/都是谁/有哪些X" 这类问题本质是列表去重查询，
        # 不应该被 DETAIL（是谁）或 COUNT（有多少）抢走。
        list_intent_keywords = [
            "分别是谁", "都有谁", "都是谁", "有哪些", "分别是哪些",
            "分别是", "列出", "列表", "名单",
        ]
        has_list_intent = any(kw in query_lower for kw in list_intent_keywords)
        if has_list_intent and operation in (
            QueryOperation.DETAIL,
            QueryOperation.COUNT,
            QueryOperation.SUM,
            QueryOperation.AVG,
        ):
            # 如果有明确的列举意图，且当前匹配的是详情/计数/金额求和，优先走列表
            operation = QueryOperation.LIST
            confidence = max(confidence, 0.75)  # 列举意图置信度设高一些
            matched_pattern = "列举意图（有哪些/都有谁/分别是谁）"

        # 检测是否包含实体（在 schema 列名中出现）
        entity_mentions = 0
        for col in schema.columns:
            col_norm = col.name.lower()
            if col_norm and col_norm in query_lower:
                entity_mentions += 1
                confidence += 0.05

        # 检测层级关系关键词
        has_hierarchy = any(kw in query for kw in HIERARCHY_RELATION_KEYWORDS)
        if has_hierarchy:
            confidence += 0.1

        # 检测是否是纯语义问题
        semantic_indicators = [
            "介绍一下", "详细说明", "什么意思", "含义", "概念",
            "解释", "关于", "相关", "类似",
        ]
        is_semantic_like = any(ind in query_lower for ind in semantic_indicators)

        # 确定最终意图
        if confidence >= 0.6 and operation != QueryOperation.UNKNOWN:
            intent = QueryIntent.STRUCTURED
            needs_sql = True
            needs_vector = False
        elif is_semantic_like and confidence < 0.5:
            intent = QueryIntent.SEMANTIC
            needs_sql = False
            needs_vector = True
        else:
            # 不确定，标记为混合，让 LLM 最终决定
            intent = QueryIntent.HYBRID
            needs_sql = confidence >= 0.4
            needs_vector = True

        confidence = min(1.0, confidence)

        return QueryPlan(
            intent=intent,
            operation=operation,
            reasoning=f"规则匹配：模式={matched_pattern}, 置信度={confidence:.2f}",
            needs_sql=needs_sql,
            needs_vector=needs_vector,
            confidence=confidence,
        )

    # ------------------------------------------------------------------
    # LLM 分析
    # ------------------------------------------------------------------

    async def _llm_based_analyze(
        self, query: str, schema: TableSchema
    ) -> Optional[QueryPlan]:
        """基于 LLM 的深度分析

        Returns:
            QueryPlan；LLM 不可用 / 超时 / 输出无法解析时返回 None（由调用方回退规则计划）
        """
        llm = self._get_llm()
        if llm is None:
            logger.warning("LLM unavailable for query planning")
            return None

        # 构造 schema 摘要（控制 token 数量）
        schema_summary = self._build_schema_summary(schema)

        system_prompt = get_prompt("router_system")
        user_prompt = f"用户问题：{query}"
        formatted_system_prompt = system_prompt.format(schema_summary=schema_summary)

        # 规划模型偶发返回空串/非 JSON（模型抖动），这里带重试；连续失败才回退规则
        max_attempts = DEFAULT_MAX_RETRIES
        for attempt in range(1, max_attempts + 1):
            try:
                llm_cfg = get_llm_config("router", settings)
                result = await llm.simple_chat(
                    prompt=user_prompt,
                    system_prompt=formatted_system_prompt,
                    temperature=llm_cfg["temperature"],
                    max_tokens=llm_cfg["max_tokens"],
                )
            except Exception as e:
                logger.warning(f"LLM planning call failed (attempt {attempt}/{max_attempts}): {e}")
                continue

            raw = (result or "").strip()
            if not raw:
                logger.warning(f"LLM planning returned empty (attempt {attempt}/{max_attempts})")
                continue

            plan = self._parse_llm_response(raw, schema)
            if plan is None:
                logger.warning(
                    f"LLM plan parse failed (attempt {attempt}/{max_attempts}): {raw[:200]!r}"
                )
                continue

            plan.raw_response = raw
            return plan

        logger.warning("LLM planning failed after retries, returning None for fallback")
        return None

    def _build_schema_summary(self, schema: TableSchema) -> str:
        """构造 schema 摘要（控制长度）"""
        lines = []
        lines.append(f"表名：{schema.sheet_name or schema.table_name}")
        lines.append(f"行数：约 {schema.row_count} 行")
        lines.append(f"列数：{len(schema.columns)} 列")
        lines.append("列信息：")

        for i, col in enumerate(schema.columns):
            # 每个列的简要描述
            aliases_str = f"（别名：{', '.join(col.aliases[:3])}）" if col.aliases else ""
            hierarchy_str = f" [层级列，级别{col.hierarchy_level}]" if col.is_hierarchy else ""
            samples_str = ""
            if col.sample_values:
                samples_str = f"，示例值：{', '.join(col.sample_values[:3])}"

            lines.append(
                f"  {i + 1}. {col.name} ({col.data_type}){hierarchy_str}{aliases_str}{samples_str}"
            )

        # 如果列太多，只显示前 20 列
        if len(schema.columns) > 20:
            lines.append(f"  ... 还有 {len(schema.columns) - 20} 列")

        return "\n".join(lines)

    def _parse_llm_response(
        self, response: str, schema: TableSchema
    ) -> Optional[QueryPlan]:
        """解析 LLM 的 JSON 响应（解析失败返回 None，交由调用方回退规则计划）"""
        # 提取 JSON（处理可能包含的 markdown 代码块或其他文字）
        json_str = self._extract_json(response)

        data = self._loads_llm_json(json_str)
        if data is None:
            logger.warning(f"Failed to parse LLM response as JSON: {json_str[:200]!r}")
            return None

        if not isinstance(data, dict):
            logger.warning(f"LLM response is not a JSON object: {type(data).__name__}")
            return None

        # 解析 intent
        intent_str = str(data.get("intent", "semantic")).lower()
        try:
            intent = QueryIntent(intent_str)
        except ValueError:
            intent = QueryIntent.SEMANTIC

        # 解析 operation
        op_str = str(data.get("operation", "unknown")).lower()
        try:
            operation = QueryOperation(op_str)
        except ValueError:
            operation = QueryOperation.UNKNOWN

        # 验证 target_column 是否在 schema 中
        target_col = str(data.get("target_column", ""))
        if target_col:
            col = schema.get_column(target_col) or schema.find_column_by_alias(target_col)
            if col:
                target_col = col.name
                target_alias = col.name
            else:
                target_col = ""
                target_alias = ""
        else:
            target_alias = ""

        # 解析 filters
        filters = []
        raw_filters = data.get("filters", [])
        if isinstance(raw_filters, list):
            for f in raw_filters:
                if not isinstance(f, dict):
                    continue
                col_name = str(f.get("column", ""))
                if not col_name:
                    continue
                # 验证列名
                col = schema.get_column(col_name) or schema.find_column_by_alias(col_name)
                if col:
                    filters.append(FilterInfo(
                        column=col.name,
                        column_alias=col.name,
                        operator=str(f.get("operator", "=")),
                        value=str(f.get("value", "")),
                        entity_name=str(f.get("value", "")),
                    ))

        # 分组聚合：规范化维度列与数值度量列
        group_by = ""
        metric_column = ""
        agg_func = "sum"
        if operation == QueryOperation.GROUP_AGG:
            raw_group = str(data.get("group_by", ""))
            gcol = schema.get_column(raw_group) or schema.find_column_by_alias(raw_group)
            group_by = gcol.name if gcol else ""
            # 度量列优先取 metric_column，其次回退到 target_column（必须为数值列）
            raw_metric = str(data.get("metric_column", "")) or target_col
            mcol = None
            if raw_metric:
                mcol = schema.get_column(raw_metric) or schema.find_column_by_alias(raw_metric)
            if mcol and mcol.data_type in NUMERIC_TYPES:
                metric_column = mcol.name
            fn = str(data.get("agg_func", "sum")).lower()
            agg_func = fn if fn in ("sum", "avg", "count", "max", "min") else "sum"
            # 分组聚合不使用行级 target_column（度量已由 metric_column 承载）
            target_col = ""
            target_alias = ""
        else:
            group_by = str(data.get("group_by", ""))

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        return QueryPlan(
            intent=intent,
            operation=operation,
            target_column=target_col,
            target_column_alias=target_alias,
            filters=filters,
            group_by=group_by,
            metric_column=metric_column,
            agg_func=agg_func,
            order_by=str(data.get("order_by", "")),
            order_dir=str(data.get("order_dir", "DESC")).upper(),
            limit=int(data.get("limit", 0) or 0),
            reasoning=str(data.get("reasoning", "")),
            needs_sql=intent in (QueryIntent.STRUCTURED, QueryIntent.HYBRID),
            needs_vector=intent in (QueryIntent.SEMANTIC, QueryIntent.HYBRID),
            confidence=confidence,
        )

    def _extract_json(self, text: str) -> str:
        """从文本中提取 JSON 字符串"""
        text = text.strip()

        # 尝试直接解析
        if text.startswith("{") and text.endswith("}"):
            return text

        # 尝试提取 ```json ... ``` 块
        json_match = re.search(r"```(?:json)?\s*\n?(\{[\s\S]*?\})\n?```", text, re.IGNORECASE)
        if json_match:
            return json_match.group(1).strip()

        # 尝试提取第一个 { 到最后一个 } 之间的内容
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return text[start : end + 1]

        return text

    @staticmethod
    def _loads_llm_json(json_str: str) -> Optional[dict]:
        """解析 LLM 返回的 JSON，容忍被 max_tokens 截断的情况（尽力修复）"""
        if not json_str:
            return None
        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        repaired = RouterAgent._repair_truncated_json(json_str)
        if repaired is None:
            return None
        try:
            return json.loads(repaired)
        except json.JSONDecodeError:
            return None

    @staticmethod
    def _close_json(text: str) -> str:
        """补齐未闭合的字符串引号与括号，使截断的 JSON 片段成为合法 JSON"""
        stack: list[str] = []
        in_str = False
        esc = False
        for ch in text:
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch in "{[":
                    stack.append(ch)
                elif ch in "}]" and stack:
                    stack.pop()
        if in_str:
            text += '"'
        text += "".join("}" if c == "{" else "]" for c in reversed(stack))
        return text

    @staticmethod
    def _repair_truncated_json(text: str) -> Optional[str]:
        """修复被截断的 JSON（模型输出超出 max_tokens 时）

        做法：在末尾附近的逗号处截断并补齐引号/括号，返回第一个可解析的前缀。
        """
        s = (text or "").strip()
        if not s.startswith("{") or len(s) > 8000:
            return None

        cut_points = [i for i, ch in enumerate(s) if ch == ","]
        cut_points.append(len(s))
        for cut in reversed(cut_points[-60:]):
            candidate = s[:cut].rstrip()
            if candidate.endswith(",") or candidate.endswith(":"):
                candidate = candidate[:-1]
            candidate = RouterAgent._close_json(candidate)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                continue
        return None

    # ------------------------------------------------------------------
    # 实体解析
    # ------------------------------------------------------------------

    def _resolve_entities(
        self, query: str, file_path: str, sheet_name: str, schema: TableSchema
    ) -> list[EntityInfo]:
        """
        解析用户问题中的实体

        优化策略：
        1. 长度过滤：实体名至少 3 个字，降低短名称误匹配率
        2. 最长匹配优先：同一列中更长的实体名优先匹配
        3. 上下文验证：如果实体出现在另一个实体内部，则保留更长的那个
        """
        entities: list[EntityInfo] = []
        seen = set()

        try:
            all_entities = self.schema_provider.get_entities(file_path, sheet_name)
        except Exception as e:
            logger.debug(f"Get entities failed: {e}")
            return entities

        if not all_entities:
            return entities

        # 按实体名长度从长到短排序（最长匹配优先）
        # 这样更具体的长实体名先匹配，不会被短实体名覆盖
        sorted_entities = sorted(all_entities, key=lambda e: len(e.name), reverse=True)

        # 记录已匹配的位置区间，避免重复匹配
        matched_spans: list[tuple[int, int]] = []

        for ent in sorted_entities:
            if not ent.name or len(ent.name) < 2:
                # 单字实体名容易误匹配，跳过（保留2字以上，包括2字人名）
                continue

            name = ent.name
            start = 0
            while True:
                pos = query.find(name, start)
                if pos == -1:
                    break
                end = pos + len(name)

                # 检查这个位置是否已经被更长的实体匹配过
                # （如果重叠了，说明是被包含的短实体，跳过）
                overlaps = False
                for ms, me in matched_spans:
                    if pos < me and end > ms:
                        overlaps = True
                        break

                if not overlaps:
                    key = f"{ent.column_name}::{ent.normalized_name}"
                    if key not in seen:
                        seen.add(key)
                        entities.append(ent)
                        matched_spans.append((pos, end))
                    break  # 每个实体只记一次

                start = pos + 1

        return entities

    # ------------------------------------------------------------------
    # 层级关系推断
    # ------------------------------------------------------------------

    def _match_target_column(self, query: str, schema: TableSchema):
        """
        从用户问题中匹配目标列

        遍历所有列（含别名），看哪个列的名称或别名出现在问题中。
        返回匹配度最高的列。
        """
        hierarchy_cols = schema.get_hierarchy_columns()
        if not hierarchy_cols:
            return None

        # 已在问题中出现的列名：用于避免"别名只是某个更长列名的一部分"的误匹配。
        # 例如"销售金额"中的"销售"是"销售经理"的别名，不应据此把目标列判为"销售经理"。
        present_col_names = [
            c.name for c in schema.columns if c.name and c.name in query
        ]

        def _shadowed(cand: str) -> bool:
            return any(cand != cn and cand in cn for cn in present_col_names)

        best_col = None
        best_match_len = 0

        for col in hierarchy_cols:
            candidates = [col.name] + col.aliases
            for cand in candidates:
                if not cand or len(cand) < 2:
                    continue
                if cand in query and not _shadowed(cand) and len(cand) > best_match_len:
                    best_match_len = len(cand)
                    best_col = col

        return best_col

    def _match_numeric_target_column(self, query: str, schema: TableSchema,
                                     operation: QueryOperation):
        """
        为 SUM/AVG 等聚合操作匹配数值目标列。

        策略：
        1. 从问题中提取可能的数值列名（如"销售金额"、"数量"、"单价"）
        2. 在 schema 的数值类型列中匹配
        3. 如果问题中没有明确提到，优先选择"金额/销售额/总额"类的列
        4. 还没有的话，选第一个数值列

        返回匹配到的 ColumnInfo，找不到返回 None。
        """
        numeric_cols = [
            c for c in schema.columns
            if c.data_type in ("INTEGER", "BIGINT", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC")
        ]
        if not numeric_cols:
            return None

        query_lower = query.lower()

        # 策略1：直接匹配问题中提到的列名
        best_col = None
        best_match_len = 0
        for col in numeric_cols:
            candidates = [col.name] + col.aliases
            for cand in candidates:
                if not cand or len(cand) < 2:
                    continue
                # 列名出现在问题中（去掉括号等符号后比较更宽松）
                cand_clean = cand.replace("（元）", "").replace("(元)", "").strip()
                if cand in query or cand_clean in query_lower:
                    if len(cand) > best_match_len:
                        best_match_len = len(cand)
                        best_col = col

        if best_col:
            return best_col

        # 策略2：SUM 操作优先选"金额/销售额/总额"类的列
        if operation == QueryOperation.SUM:
            # 金额类关键词（优先级从高到低）
            amount_keywords = [
                "金额", "销售额", "销售金额", "总额", "总价", "总金额",
                "收入", "营收", "回款", "利润", "毛利",
                "数量", "销量", "总数量", "个数",
            ]
            for kw in amount_keywords:
                for col in numeric_cols:
                    if kw in col.name or any(kw in a for a in col.aliases):
                        return col

        # 策略3：AVG 操作优先选"单价/金额/数量"类的列
        if operation == QueryOperation.AVG:
            avg_keywords = [
                "单价", "价格", "金额", "平均", "均价",
                "数量", "销量",
            ]
            for kw in avg_keywords:
                for col in numeric_cols:
                    if kw in col.name or any(kw in a for a in col.aliases):
                        return col

        # 策略4：兜底 - 返回第一个数值列
        return numeric_cols[0] if numeric_cols else None

    # ------------------------------------------------------------------
    # 分组聚合（GROUP_AGG）推断
    # ------------------------------------------------------------------

    @staticmethod
    def _cn_num_to_int(text: str) -> int:
        """中文/阿拉伯数字 → 整数（支持 一 ~ 九十九 的常见写法）"""
        if not text:
            return 0
        text = text.strip()
        if text.isdigit():
            return int(text)
        digits = {
            "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
        }
        if text == "十":
            return 10
        if "十" in text:
            left, _, right = text.partition("十")
            tens = digits.get(left, 1) if left else 1
            ones = digits.get(right, 0) if right else 0
            return tens * 10 + ones
        return digits.get(text, 0)

    def _match_metric_column(self, query: str, schema: TableSchema):
        """匹配 GROUP_AGG 的数值度量列（如"销售金额"）

        策略：
        1. 显式匹配：列名/别名出现在问题中（最长优先）
        2. 业务优先级推断：问题未点名度量列时，按"金额/销售额 > 数量/销量"优先级选列
        3. 不做"任意数值列"兜底 → 返回 None，交由调用方退化为分组计数
        """
        numeric_cols = [
            c for c in schema.columns if c.data_type in NUMERIC_TYPES
        ]
        if not numeric_cols:
            return None

        query_lower = query.lower()

        # 策略1：显式匹配问题中提到的数值列名
        best_col = None
        best_len = 0
        for col in numeric_cols:
            for cand in [col.name] + list(col.aliases or []):
                if not cand or len(cand) < 2:
                    continue
                cand_clean = cand.replace("（元）", "").replace("(元)", "").strip()
                if cand in query or cand_clean in query_lower:
                    if len(cand) > best_len:
                        best_len = len(cand)
                        best_col = col
        if best_col:
            return best_col

        # 策略2：业务优先级推断（金额类优先，其次数量类）
        for kw in (
            "销售金额", "销售额", "金额", "营业额", "业绩", "总额", "总金额",
            "收入", "营收", "回款", "利润", "毛利", "销售",
            "数量", "销量", "总数量", "单价", "价格",
        ):
            for col in numeric_cols:
                if kw in col.name or any(kw in (a or "") for a in (col.aliases or [])):
                    return col

        return None

    def _match_dimension_column(self, query: str, schema: TableSchema, exclude: str | None = None):
        """匹配 GROUP_AGG 的分组维度列（分类/层级列，最长匹配优先）

        - 排除数值列（维度不可能是度量列）
        - 优先层级/分类列；名称匹配优先于别名匹配
        """
        candidates = schema.get_hierarchy_columns()
        if not candidates:
            candidates = [c for c in schema.columns if c.data_type not in NUMERIC_TYPES]

        best_col = None
        best_key: tuple | None = None
        for col in candidates:
            if col.data_type in NUMERIC_TYPES:
                continue
            if exclude and col.name == exclude:
                continue
            # (匹配文本, 权重)：列名权重 2，别名权重 1
            cand_pairs = [(col.name, 2)] + [(a, 1) for a in (col.aliases or [])]
            for cand, weight in cand_pairs:
                if not cand or len(cand) < 2:
                    continue
                if cand in query:
                    # 优先"更长"的匹配（更长意味着更具体，如"客户经理"优于"客户"），
                    # 长度相同时再看是列名还是别名
                    key = (len(cand), weight)
                    if best_key is None or key > best_key:
                        best_key = key
                        best_col = col
        return best_col

    def _maybe_group_agg(
        self,
        query: str,
        schema: TableSchema,
        plan: QueryPlan,
        inherited_state: Optional[dict] = None,
    ) -> Optional[QueryPlan]:
        """把（可能被误判为 TOP_N/GROUP_COUNT 的）计划升级为 GROUP_AGG。

        触发条件：
        A. 命中排名/极值词（最多/最高/前N/排名…）且能确定分组维度
           → 分组聚合排名；无明确数值度量时退化为"分组计数排名"
        B. 命中分组统计词（按…分组/每个/各个…）且同时命中维度与数值度量 → 分组聚合

        维度（分析对象）也是可继承的"槽位"：当前问题未给出维度时，沿用上一轮的
        group_by / metric / order_dir / limit（如"不限制销售客户，销售额前三"仍按客户经理排名）。

        返回新的 QueryPlan（成功）或 None（不适用/保持原计划）。
        """
        if not getattr(settings, "smart_query_group_agg_enabled", True):
            return None
        if not query:
            return None
        # 幂等：已经是分组聚合计划则保持
        if plan.operation == QueryOperation.GROUP_AGG and plan.group_by:
            return None

        has_rank = bool(GROUP_RANK_PATTERN.search(query))
        has_group_stats = bool(GROUP_STATS_PATTERN.search(query))
        if not (has_rank or has_group_stats):
            return None

        # 上一轮的分组槽位（用于继承）
        inherited_group_by = ""
        inherited_metric = ""
        inherited_limit = 0
        inherited_order_dir = ""
        if isinstance(inherited_state, dict):
            inherited_group_by = str(inherited_state.get("group_by") or "")
            inherited_metric = str(inherited_state.get("metric") or "")
            inherited_order_dir = str(inherited_state.get("order_dir") or "")
            try:
                inherited_limit = int(inherited_state.get("limit") or 0)
            except (TypeError, ValueError):
                inherited_limit = 0

        metric_col = self._match_metric_column(query, schema)
        if metric_col is None and inherited_metric:
            metric_col = schema.get_column(inherited_metric) or schema.find_column_by_alias(
                inherited_metric
            )

        dim_col = self._match_dimension_column(
            query, schema, exclude=metric_col.name if metric_col else None
        )
        # 当前问题未给出维度 → 继承上一轮的分析对象
        if dim_col is None and inherited_group_by:
            dim_col = schema.get_column(inherited_group_by) or schema.find_column_by_alias(
                inherited_group_by
            )
        if dim_col is None:
            return None

        # 无排名词（仅分组统计词）时，必须有明确数值度量，避免误伤列表类问题
        if not has_rank and metric_col is None:
            return None

        # 排序方向：当前问题显式"最少/最低"优先，其次继承，最后默认降序
        if GROUP_ASC_PATTERN.search(query):
            order_dir = "ASC"
        elif inherited_order_dir in ("ASC", "DESC"):
            order_dir = inherited_order_dir
        else:
            order_dir = "DESC"

        # 返回条数 N：当前问题显式值 > （仅排名类才允许）继承值 > 默认
        limit = 0
        m = GROUP_LIMIT_PATTERN.search(query)
        if m:
            limit = self._cn_num_to_int(m.group(1) or m.group(2) or "")
        # 仅"排名/极值"类问题才继承上一轮的 Top-N；纯分组统计（每个X的Y）应返回全部分组
        if limit <= 0 and has_rank and inherited_limit > 0:
            limit = inherited_limit
        if limit <= 0:
            if has_rank:
                # "哪个/谁" → 单个最优；否则给一个合理的 Top10
                limit = 1 if GROUP_SINGLE_HINT.search(query) else 10
            else:
                limit = 0  # 分组统计：返回全部分组

        agg_func = "sum" if metric_col is not None else "count"
        metric_name = metric_col.name if metric_col is not None else ""
        metric_desc = metric_name if metric_name else "记录数"

        return QueryPlan(
            intent=QueryIntent.STRUCTURED,
            operation=QueryOperation.GROUP_AGG,
            group_by=dim_col.name,
            metric_column=metric_name,
            agg_func=agg_func,
            order_dir=order_dir,
            limit=limit,
            filters=list(plan.filters),
            entities=list(plan.entities),
            target_column="",
            target_column_alias="",
            reasoning=(
                f"分组聚合：按「{dim_col.name}」分组，聚合「{metric_desc}」"
                f"（{agg_func}），{order_dir}，limit={limit}"
            ),
            needs_sql=True,
            needs_vector=False,
            confidence=max(0.85, plan.confidence),
        )

    def _correct_top_n_target(self, query: str, schema: TableSchema, plan: QueryPlan) -> None:
        """TOP_N 纠偏：目标列必须为数值列，否则"排序取前N"没有意义。

        - 目标列非数值（如误匹配到文本维度列"业务员"）→ 改用数值度量列
        - 无任何数值列 → 降级为 LIST，避免对文本列做无意义排序
        """
        if plan.operation != QueryOperation.TOP_N:
            return

        target = plan.target_column
        is_numeric = False
        if target:
            col = schema.get_column(target) or schema.find_column_by_alias(target)
            if col and col.data_type in NUMERIC_TYPES:
                is_numeric = True

        if is_numeric:
            return

        metric = self._match_metric_column(query, schema)
        if metric is not None:
            plan.target_column = metric.name
            plan.target_column_alias = metric.name
        else:
            plan.operation = QueryOperation.LIST
            plan.target_column = ""
            plan.target_column_alias = ""

    def _finalize_target_column(self, plan: QueryPlan, schema: TableSchema) -> None:
        """
        目标列后处理：修正各种目标列相关的问题

        处理以下场景：
        1. DETAIL/EXISTS 操作：不应该有目标列（是查整行详情/是否存在）
        2. LIST/DETAIL + 目标列与实体列同名：用户是想查这个实体的详情，不是要列出所有值
        """
        # --- 场景1：DETAIL/EXISTS 操作不应该有目标列 ---
        # DETAIL 是查整行详情，EXISTS 是判断是否存在，都不需要目标列参与检索
        if plan.operation in (QueryOperation.DETAIL, QueryOperation.EXISTS):
            target_col = plan.target_column
            if target_col and plan.entities:
                # 有目标列 + 有实体 → 把目标列+实体转为过滤条件
                matching_entities = [
                    e for e in plan.entities
                    if e.column_name == target_col and e.name
                ]
                if matching_entities:
                    # 实体与目标列同名（如"余宇诺的相关信息"，实体"余宇诺"落在"姓名"列）
                    # → 用户想要的是整行详情，转成过滤条件
                    for ent in matching_entities:
                        existing = any(
                            f.column == target_col and f.value == ent.name
                            for f in plan.filters
                        )
                        if not existing:
                            plan.filters.append(FilterInfo(
                                column=target_col,
                                column_alias=ent.column_alias or target_col,
                                value=ent.name,
                                entity_name=ent.name,
                            ))
                elif not plan.projection_column:
                    # 目标列与实体所在列不同（如"胡兰桂在哪个部门"：实体在"姓名"列、目标列是"部门"）
                    # → 这是"问某实体的某属性"，记录为投影列供合成阶段定向回答。
                    # 不参与检索，因此不会影响过滤条件构造。
                    plan.projection_column = target_col
            # 清空目标列（检索仍按整行返回，保持原有行为）
            plan.target_column = ""
            plan.target_column_alias = ""
            return

        # --- 场景2：LIST 操作 + 目标列与实体列同名 ---
        # 例如"余宇诺的相关信息" → 目标列被匹配为"姓名"，实体"余宇诺"也在"姓名"列下
        # 用户实际是想查余宇诺的详情，不是要列出所有姓名 → 转为 DETAIL
        if plan.operation == QueryOperation.LIST \
                and plan.target_column \
                and plan.entities:
            target_col = plan.target_column
            matching_entities = [
                e for e in plan.entities
                if e.column_name == target_col and e.name
            ]
            if matching_entities:
                # 转为 DETAIL 查询
                plan.operation = QueryOperation.DETAIL
                plan.target_column = ""
                plan.target_column_alias = ""
                for ent in matching_entities:
                    existing = any(
                        f.column == target_col and f.value == ent.name
                        for f in plan.filters
                    )
                    if not existing:
                        plan.filters.append(FilterInfo(
                            column=target_col,
                            column_alias=ent.column_alias or target_col,
                            value=ent.name,
                            entity_name=ent.name,
                        ))

    def _infer_hierarchy_relation(self, plan: QueryPlan, schema: TableSchema) -> None:
        """
        推断层级关系 + 自动补全目标列和过滤条件

        功能：
        1. 根据问题关键词推断目标列（如"业务员"→业务员列，"经理"→特区经理列）
        2. 根据实体自动添加层级过滤条件（如实体"张浩"是销售总监 → WHERE 销售总监='张浩'）
        3. 识别"手下/下属/下面"等层级关系词汇

        例如："张浩手下有多少业务员"
        - 实体：张浩（销售总监，level 0）
        - 目标列：业务员（level 4）
        - 关系：销售总监 → 业务员
        - 自动添加 filter: 销售总监 = '张浩'
        """
        hierarchy_cols = schema.get_hierarchy_columns()
        if not hierarchy_cols:
            return

        # ---- Step 1: 推断目标列（如果还没有） ----
        if not plan.target_column and plan.operation in (
            QueryOperation.COUNT,
            QueryOperation.LIST,
            QueryOperation.DETAIL,
        ):
            # 注意：目标列匹配需要 query 上下文，但此方法签名没有 query 参数
            # 目标列匹配已经在 analyze() 方法的 _match_target_column() 中完成了
            # 这里只处理"有实体时默认取最底层列"的兜底逻辑
            #
            # 如果有实体，且操作是 COUNT/LIST，目标列通常是最底层的层级列
            # 比如"张浩手下有多少人" → 默认统计最底层的"业务员"
            if plan.entities and not plan.target_column:
                # 找最低层级的列（最大的 hierarchy_level）
                lowest_col = max(hierarchy_cols, key=lambda c: c.hierarchy_level)
                # 如果最低层级列的别名包含"员"之类的词，很可能就是目标
                if any("员" in a or "人" in a for a in lowest_col.aliases) or \
                   "员" in lowest_col.name or "人" in lowest_col.name:
                    plan.target_column = lowest_col.name
                    plan.target_column_alias = lowest_col.name

        # ---- Step 2: 根据实体自动添加过滤条件 ----
        if plan.entities:
            for ent in plan.entities:
                if not ent.column_name:
                    continue

                # 跳过目标列本身：目标列是要统计/列出的对象，不应作为过滤条件
                # 例如："张浩手下有多少业务员" 中，"业务员" 是目标列，不是过滤条件
                if plan.target_column and ent.column_name == plan.target_column:
                    continue

                # 检查是否已有相同列的 filter
                existing_filter = any(
                    f.column == ent.column_name
                    for f in plan.filters
                )

                if not existing_filter:
                    plan.filters.append(FilterInfo(
                        column=ent.column_name,
                        column_alias=ent.column_alias or ent.column_name,
                        value=ent.name,
                        entity_name=ent.name,
                    ))

        # 清理：移除 filters 中列与目标列相同的过滤条件（防止 LLM 误添加）
        if plan.target_column and plan.filters:
            plan.filters = [
                f for f in plan.filters
                if f.column != plan.target_column
            ]

        # ---- Step 3: 建立层级关系 ----
        if plan.entities and plan.target_column:
            target_col = schema.get_column(plan.target_column)
            if not target_col:
                target_col = schema.find_column_by_alias(plan.target_column)
                if target_col:
                    plan.target_column = target_col.name

            if target_col and target_col.is_hierarchy:
                for ent in plan.entities:
                    if ent.hierarchy_level >= 0 and ent.hierarchy_level < target_col.hierarchy_level:
                        # 实体在更高层级，目标在更低层级 → 层级关系
                        plan.hierarchy_relation = f"{ent.column_name} → {target_col.name}"
                        break

    # ------------------------------------------------------------------
    # 计划融合
    # ------------------------------------------------------------------

    def _merge_plans(self, rule_plan: QueryPlan, llm_plan: QueryPlan) -> QueryPlan:
        """融合规则和 LLM 的分析结果"""
        # 策略：取置信度更高的那个
        # 但如果两个都指向同一类型，提升置信度

        if llm_plan.confidence >= rule_plan.confidence:
            final = llm_plan
        else:
            final = rule_plan

        # 如果两者的 intent 一致，提升置信度
        if llm_plan.intent == rule_plan.intent and final.confidence < 0.95:
            final.confidence = min(0.95, final.confidence + 0.1)

        # 如果规则说结构化，LLM 说语义，取保守策略（混合）
        if (rule_plan.intent == QueryIntent.STRUCTURED and
                llm_plan.intent == QueryIntent.SEMANTIC):
            final.intent = QueryIntent.HYBRID
            final.needs_sql = True
            final.needs_vector = True
            final.confidence = 0.5

        return final

    # ------------------------------------------------------------------
    # 智能 top_k 计算
    # ------------------------------------------------------------------

    def _calc_suggested_top_k(self, plan: QueryPlan) -> int:
        """
        根据查询类型智能计算建议的检索数量。

        原则：
        - 聚合类（SUM/COUNT/AVG）：SQL 路径不受 top_k 限制，但向量路径需要更多候选
        - 列表类：取更多（20），保证信息完整
        - Top N：从问题中提取，默认 10
        - 详情/存在性：少量即可
        - 语义类：5 条（默认，精准不冗余）
        """
        import re

        op = plan.operation

        # --- 聚合类：SQL 精确返回 1 条结果，但向量兜底需要更多候选 ---
        if op == QueryOperation.COUNT:
            return 20  # 列表明细需要更多数据支撑
        if op in (QueryOperation.SUM, QueryOperation.AVG):
            return 20  # 向量路径下尽量多取，SQL 路径不受影响
        if op == QueryOperation.GROUP_AGG:
            return 20  # SQL 由 limit 控制，向量兜底多取候选

        # --- 列表/分组统计类：信息要尽量完整 ---
        if op in (QueryOperation.LIST, QueryOperation.GROUP_COUNT, QueryOperation.DETAIL):
            return 20

        # --- Top N 类：从问题中提取 N 值 ---
        if op == QueryOperation.TOP_N:
            # 尝试从 plan.limit 或原始问题中提取
            if plan.limit and plan.limit > 0:
                return min(plan.limit, 100)
            # 从 reasoning 或 raw_response 中提取（简化：返回默认 10）
            return 10

        # --- 存在性判断：1 条就够 ---
        if op == QueryOperation.EXISTS:
            return 1

        # --- 混合 / 语义类 / 未知：默认 5 条，精准不冗余 ---
        if plan.intent == QueryIntent.SEMANTIC:
            return 5
        if plan.intent == QueryIntent.HYBRID:
            return 10  # 混合模式多取一些，保证两路都有足够数据

        # 兜底默认值
        return 5
