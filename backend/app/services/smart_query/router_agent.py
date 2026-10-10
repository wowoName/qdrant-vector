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

import json
import re
from typing import Optional

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.llm.factory import create_llm
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
    # 求和
    (r"总和|合计|共计|总计金额|总金额|总销量|销售额", QueryOperation.SUM),
    # 平均值
    (r"平均|均值|平均值", QueryOperation.AVG),
    # 详情查询（注意："分别是谁/都有谁/都是谁"属于列表类，不在这里匹配）
    (r"(?<!分别)(?<!都)(?<!各)(?<!还)是谁|(?<!分别)(?<!都)(?<!各)(?<!还)是什么|详细信息|详情|介绍.*下|介绍一下", QueryOperation.DETAIL),
    # 是否存在
    (r"有没有|是否有|存在不存在|在不在", QueryOperation.EXISTS),
]

# 层级关系关键词（"手下"、"下属"、"下面"、"负责的"等）
HIERARCHY_RELATION_KEYWORDS = [
    "手下", "下属", "下面", "底下", "负责的", "管的", "带的",
    "属于", "归", "在...下面", "在...手下", "的人", "的员工",
    "的业务员", "的销售", "的人员",
]

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

    def _get_llm(self):
        """获取 LLM 实例（懒加载）"""
        if self._llm is None:
            try:
                self._llm = create_llm(
                    provider=getattr(settings, "rewrite_llm_provider", None) or settings.llm_provider,
                    model=getattr(settings, "rewrite_llm_model", None) or None,
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
    ) -> QueryPlan:
        """
        分析用户问题，生成查询计划

        Args:
            query: 用户问题
            file_path: Excel/CSV 文件路径
            sheet_name: Sheet 名
            schema: 可选，预加载的表结构

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

        # Step 1: 规则快速匹配（低成本、高确定性）
        rule_based_plan = self._rule_based_analyze(query, schema)

        # Step 2: 实体解析（所有路径都执行，不依赖 LLM）
        try:
            entities = self._resolve_entities(query, file_path, sheet_name, schema)
            rule_based_plan.entities = entities
        except Exception as e:
            logger.debug(f"Entity resolution failed: {e}")

        # Step 3: 层级关系推断（所有路径都执行）
        # 先尝试从 query 中匹配目标列
        # 注意：DETAIL 操作不匹配目标列，因为 DETAIL 是查整行详情，目标列应该为空
        # 如果 DETAIL 有目标列，会导致同名列的实体无法转为过滤条件
        if not rule_based_plan.target_column and \
           rule_based_plan.operation not in (QueryOperation.DETAIL, QueryOperation.EXISTS):
            matched_target = self._match_target_column(query, schema)
            if matched_target:
                rule_based_plan.target_column = matched_target.name
                rule_based_plan.target_column_alias = matched_target.name

        self._infer_hierarchy_relation(rule_based_plan, schema)

        # 针对 SUM/AVG 操作的目标列推断（数值列匹配）
        if rule_based_plan.operation in (QueryOperation.SUM, QueryOperation.AVG):
            # SUM/AVG 的目标列必须是数值列
            # 如果当前目标列不是数值列（比如匹配到了维度列"销售经理"），需要重新匹配
            current_target = rule_based_plan.target_column
            current_is_numeric = False
            if current_target:
                col = schema.get_column(current_target)
                if col and col.data_type in ('BIGINT', 'DOUBLE', 'DECIMAL', 'INT', 'INTEGER', 'FLOAT', 'NUMBER'):
                    current_is_numeric = True

            if not current_is_numeric:
                numeric_target = self._match_numeric_target_column(
                    query, schema, rule_based_plan.operation
                )
                if numeric_target:
                    # 原来的维度目标列 + 实体 → 转化为过滤条件
                    if current_target and rule_based_plan.entities:
                        for ent in rule_based_plan.entities:
                            if ent.column_name == current_target and ent.name:
                                # 检查是否已有相同的 filter
                                existing = any(
                                    f.column == current_target and f.value == ent.name
                                    for f in rule_based_plan.filters
                                )
                                if not existing:
                                    rule_based_plan.filters.append(FilterInfo(
                                        column=current_target,
                                        column_alias=ent.column_alias or current_target,
                                        value=ent.name,
                                        entity_name=ent.name,
                                    ))

                    rule_based_plan.target_column = numeric_target.name
                    rule_based_plan.target_column_alias = numeric_target.name
                    # 提升置信度（因为找到了明确的聚合目标列）
                    if rule_based_plan.confidence < 0.95:
                        rule_based_plan.confidence = min(0.95, rule_based_plan.confidence + 0.08)

        # 统一的目标列后处理（修正各种目标列相关的问题）
        self._finalize_target_column(rule_based_plan, schema)

        # 如果规则匹配的置信度足够高，直接返回
        if rule_based_plan.confidence >= RULE_BASED_CONFIDENCE_THRESHOLD:
            # 计算 suggested_top_k
            rule_based_plan.suggested_top_k = self._calc_suggested_top_k(rule_based_plan)
            logger.debug(
                f"Rule-based intent detection: {rule_based_plan.intent}, "
                f"op={rule_based_plan.operation}, "
                f"confidence={rule_based_plan.confidence:.2f}, "
                f"entities={[e.name for e in rule_based_plan.entities]}, "
                f"target={rule_based_plan.target_column}, "
                f"suggested_top_k={rule_based_plan.suggested_top_k}"
            )
            return rule_based_plan

        # Step 4: LLM 深度分析（高成本、更准确）
        llm_plan = await self._llm_based_analyze(query, schema)

        # 融合规则和 LLM 的结果
        final_plan = self._merge_plans(rule_based_plan, llm_plan)

        # 确保实体信息传递到最终计划
        if not final_plan.entities and rule_based_plan.entities:
            final_plan.entities = rule_based_plan.entities

        # 如果还没有层级关系，再推断一次
        if not final_plan.hierarchy_relation:
            # 先确保有目标列
            if not final_plan.target_column:
                matched_target = self._match_target_column(query, schema)
                if matched_target:
                    final_plan.target_column = matched_target.name
                    final_plan.target_column_alias = matched_target.name

            self._infer_hierarchy_relation(final_plan, schema)

        # 针对 SUM/AVG 操作的目标列推断（数值列匹配）
        if final_plan.operation in (QueryOperation.SUM, QueryOperation.AVG):
            # SUM/AVG 的目标列必须是数值列
            current_target = final_plan.target_column
            current_is_numeric = False
            if current_target:
                col = schema.get_column(current_target)
                if col and col.data_type in ('BIGINT', 'DOUBLE', 'DECIMAL', 'INT', 'INTEGER', 'FLOAT', 'NUMBER'):
                    current_is_numeric = True

            if not current_is_numeric:
                numeric_target = self._match_numeric_target_column(
                    query, schema, final_plan.operation
                )
                if numeric_target:
                    # 原来的维度目标列 + 实体 → 转化为过滤条件
                    if current_target and final_plan.entities:
                        for ent in final_plan.entities:
                            if ent.column_name == current_target and ent.name:
                                existing = any(
                                    f.column == current_target and f.value == ent.name
                                    for f in final_plan.filters
                                )
                                if not existing:
                                    final_plan.filters.append(FilterInfo(
                                        column=current_target,
                                        column_alias=ent.column_alias or current_target,
                                        value=ent.name,
                                        entity_name=ent.name,
                                    ))

                    final_plan.target_column = numeric_target.name
                    final_plan.target_column_alias = numeric_target.name
                    # 提升置信度
                    if final_plan.confidence < 0.95:
                        final_plan.confidence = min(0.95, final_plan.confidence + 0.08)

        # 统一的目标列后处理（修正各种目标列相关的问题）
        self._finalize_target_column(final_plan, schema)

        # 兜底：规则阶段识别出的"属性投影"在 LLM 融合后可能丢失，补回
        if not final_plan.projection_column and rule_based_plan.projection_column:
            final_plan.projection_column = rule_based_plan.projection_column

        # Step 5: 根据查询类型计算建议的 top_k
        final_plan.suggested_top_k = self._calc_suggested_top_k(final_plan)

        logger.info(
            f"Query analysis completed: intent={final_plan.intent}, "
            f"op={final_plan.operation}, "
            f"confidence={final_plan.confidence:.2f}, "
            f"suggested_top_k={final_plan.suggested_top_k}"
        )

        return final_plan

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
        if has_list_intent and operation in (QueryOperation.DETAIL, QueryOperation.COUNT):
            # 如果有明确的列举意图，且当前匹配的是详情或计数，优先走列表
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

    async def _llm_based_analyze(self, query: str, schema: TableSchema) -> QueryPlan:
        """基于 LLM 的深度分析"""
        llm = self._get_llm()
        if llm is None:
            # LLM 不可用，返回一个保守的默认值
            return QueryPlan(
                intent=QueryIntent.SEMANTIC,
                operation=QueryOperation.UNKNOWN,
                reasoning="LLM不可用，降级为语义检索",
                needs_vector=True,
                confidence=0.3,
            )

        # 构造 schema 摘要（控制 token 数量）
        schema_summary = self._build_schema_summary(schema)

        system_prompt = """你是一个专业的查询分析助手。请根据用户的问题和数据表结构，判断查询意图并生成查询计划。

数据表结构信息：
{schema_summary}

请分析用户的问题，输出严格的 JSON 格式，包含以下字段：
{{
  "intent": "structured | semantic | hybrid | direct_answer",
  "operation": "count | list | detail | group_count | sum | avg | top_n | exists | unknown",
  "target_column": "要统计或列出的目标列名（从表结构中选择，用原始列名）",
  "filters": [
    {{"column": "列名", "operator": "= | != | > | < | >= | <= | LIKE", "value": "过滤值"}}
  ],
  "group_by": "分组列名（分组统计时）",
  "order_by": "排序列名",
  "order_dir": "ASC | DESC",
  "limit": 0,
  "confidence": 0.0到1.0之间的置信度,
  "reasoning": "你的分析推理过程"
}}

注意事项：
1. intent 取值说明：
   - structured: 可以直接用 SQL 回答的结构化查询（统计、列表、精确匹配）
   - semantic: 需要语义理解的查询（解释、描述、模糊查找）
   - hybrid: 既有结构化条件又有语义条件的混合查询
   - direct_answer: 不需要检索数据就能直接回答的问题（问候、系统问题等）
2. 只输出 JSON，不要有其他解释性文字
3. 如果无法确定 target_column 或 filters，留空字符串或空数组
4. 所有列名必须从给定的表结构中选择，使用原始列名
5. confidence 表示你对这个分析结果的把握程度"""

        user_prompt = f"用户问题：{query}"

        try:
            result = await llm.simple_chat(
                prompt=user_prompt,
                system_prompt=system_prompt.format(schema_summary=schema_summary),
                temperature=0.1,
                max_tokens=800,
            )

            plan = self._parse_llm_response(result, schema)
            plan.raw_response = result
            return plan

        except Exception as e:
            logger.warning(f"LLM-based query analysis failed: {e}")
            return QueryPlan(
                intent=QueryIntent.SEMANTIC,
                operation=QueryOperation.UNKNOWN,
                reasoning=f"LLM分析失败，降级为语义检索: {e}",
                needs_vector=True,
                confidence=0.2,
            )

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

    def _parse_llm_response(self, response: str, schema: TableSchema) -> QueryPlan:
        """解析 LLM 的 JSON 响应"""
        # 提取 JSON（处理可能包含的 markdown 代码块或其他文字）
        json_str = self._extract_json(response)

        try:
            data = json.loads(json_str)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse LLM response as JSON: {e}")
            return QueryPlan(
                intent=QueryIntent.SEMANTIC,
                operation=QueryOperation.UNKNOWN,
                reasoning=f"JSON解析失败: {e}",
                needs_vector=True,
                confidence=0.2,
            )

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

        confidence = float(data.get("confidence", 0.5))
        confidence = max(0.0, min(1.0, confidence))

        return QueryPlan(
            intent=intent,
            operation=operation,
            target_column=target_col,
            target_column_alias=target_alias,
            filters=filters,
            group_by=str(data.get("group_by", "")),
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

        best_col = None
        best_match_len = 0

        for col in hierarchy_cols:
            candidates = [col.name] + col.aliases
            for cand in candidates:
                if not cand or len(cand) < 2:
                    continue
                if cand in query and len(cand) > best_match_len:
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
