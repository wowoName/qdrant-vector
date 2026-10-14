"""
查询分解器（Query Decomposer）

将用户的复合问题拆解为多个独立子查询，每个子查询对应一个明确的信息需求。
支持规则快速路径 + LLM 兜底分解。

典型场景：
- "刘敏一共有多少条销售记录，以及总金额是多少"
  → 子问题1：刘敏有多少条销售记录（COUNT）
  → 子问题2：刘敏的销售总金额是多少（SUM）

- "王强的部门以及销售金额"
  → 子问题1：王强在哪个部门（DETAIL）
  → 子问题2：王强的销售金额是多少（SUM）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.llm.factory import create_llm
from .constants import get_llm_config
from .prompts import get_prompt

logger = get_logger(__name__)


@dataclass
class SubQuery:
    """子查询"""

    query: str  # 子查询文本（完整的、可独立执行的问题）
    description: str = ""  # 子查询的语义描述（如"统计销售金额"）
    entity: str = ""  # 核心实体（如人名、部门名），用于关联
    source: str = "rule"  # 来源：rule / llm

    def __repr__(self):
        return f"SubQuery(query='{self.query}', source={self.source})"


@dataclass
class DecompositionResult:
    """分解结果"""

    original_query: str  # 原始问题
    sub_queries: list[SubQuery] = field(default_factory=list)
    is_multi_intent: bool = False  # 是否为多意图问题
    method: str = "single"  # 分解方式：single（无需分解） / rule（规则） / llm（大模型）
    error: str = ""  # 拆解失败时可直接展示给用户的提示

    @property
    def count(self) -> int:
        return len(self.sub_queries)


# ---------------------------------------------------------------------------
# 分解器
# ---------------------------------------------------------------------------


class QueryDecomposer:
    """
    查询分解器

    策略：
    1. 规则快速路径：明确的并列结构直接拆分（0延迟、不花token）
    2. LLM 兜底：复杂/模糊的复合问题交给LLM判断
    3. 单意图问题不分解，走原流程（无额外开销）
    """

    # 规则：并列连接词（前后都是完整语义单元时拆分）
    # 强并列：以及、同时、分别、还有
    # 中并列：和、与、及（需要判断前后都是完整需求）
    _STRONG_CONJUNCTIONS = ["以及", "同时", "分别是", "还有", "分别为", "各是多少"]
    _MEDIUM_CONJUNCTIONS = ["和", "与", "及"]

    # 指标类关键词（出现多个时，很可能是多指标查询）
    _METRIC_KEYWORDS = [
        "金额",
        "数量",
        "总数",
        "合计",
        "总计",
        "平均",
        "条数",
        "记录数",
        "销售额",
        "销售量",
        "个数",
        "人数",
        "次数",
    ]

    # 单意图模式：虽然有连接词但是单个需求
    # 例如"销售金额和数量的关系" → 单意图
    # "各区域的销售总额分别是多少" → 分组统计，单意图
    _SINGLE_INTENT_PATTERNS = [
        r"的关系",
        r"的对比",
        r"的比较",
        r"占比",
        r"比例",
    ]

    # 分组统计模式：X分别是多少 / 各X的Y → 单意图（分组统计）
    _GROUP_PATTERNS = [
        r"各.+?分别",
        r"每.+?分别",
        r"分别是多少",
        r"分别有多少",
        r"各自",
    ]

    def __init__(self):
        self._llm: object | None = None

    def _get_llm(self) -> object | None:
        """获取 LLM 实例（懒加载）"""
        if self._llm is None:
            try:
                llm_cfg = get_llm_config("decompose", settings)
                self._llm = create_llm(
                    provider=llm_cfg["provider"],
                    model=llm_cfg["model"],
                )
            except Exception as e:
                logger.warning(f"Failed to create LLM for QueryDecomposer: {e}")
                self._llm = None
        return self._llm

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    async def decompose(self, query: str) -> DecompositionResult:
        """
        分解用户问题为子查询

        Args:
            query: 用户原始问题

        Returns:
            DecompositionResult
            - 单意图：sub_queries 只有1个，is_multi_intent=False
            - 多意图：sub_queries 有多个，is_multi_intent=True
        """
        if not query or len(query.strip()) < 4:
            return DecompositionResult(
                original_query=query,
                sub_queries=[SubQuery(query=query, description="", source="rule")],
                is_multi_intent=False,
                method="single",
            )

        # 第一步：规则快速路径
        rule_result = self._try_rule_decompose(query)
        if rule_result is not None and rule_result.is_multi_intent:
            sub_q_names = "、".join([f"「{sq.query}」" for sq in rule_result.sub_queries])
            logger.info(
                f"【任务拆解-规则】检测到多意图问题，共拆解为 {len(rule_result.sub_queries)} 个子任务："
                f"{sub_q_names}"
            )
            return rule_result

        # 第二步：LLM 分解（仅当规则没判断为多意图时才走）
        # 避免：规则已经判断是单意图了，LLM 又拆成多个
        llm = self._get_llm()
        failure_message = ""
        if llm is not None:
            try:
                llm_result = await self._llm_decompose(query, llm)
                if llm_result is None:
                    failure_message = "大模型任务拆解失败：模型返回的内容无效。请稍后重试，或将问题拆成多个简单问题分别提问。"
                if llm_result is not None and llm_result.is_multi_intent:
                    sub_q_names = "、".join([f"「{sq.query}」" for sq in llm_result.sub_queries])
                    logger.info(
                        f"【任务拆解-LLM】检测到多意图问题，共拆解为 {len(llm_result.sub_queries)} 个子任务："
                        f"{sub_q_names}"
                    )
                    return llm_result
            except Exception as e:
                logger.warning(f"LLM 任务拆解失败：{e}")
                failure_message = "大模型任务拆解调用失败，暂时无法处理本次问题。请稍后重试；若持续失败，请联系管理员检查模型服务、API 密钥及额度。"
        else:
            failure_message = "大模型任务拆解服务不可用。请联系管理员检查模型配置后重试。"

        if failure_message:
            return DecompositionResult(original_query=query, method="failed", error=failure_message)

        # 第三步：单意图，不分解
        result = DecompositionResult(
            original_query=query,
            sub_queries=[SubQuery(query=query, description="", source="rule")],
            is_multi_intent=False,
            method="single",
        )
        logger.info(f"【任务拆解-单意图】无需拆解，按单个任务处理：「{query[:50]}」")
        return result

    # ------------------------------------------------------------------
    # 规则分解
    # ------------------------------------------------------------------

    def _try_rule_decompose(self, query: str) -> DecompositionResult | None:
        """
        规则快速分解：处理明确的并列结构

        能拆就拆，不能拆返回 None（交给 LLM 或不分解）
        """
        q = query.strip()

        # 先判断是否为分组统计/列举意图（单意图，不分解）
        if self._is_group_statement(q):
            return DecompositionResult(
                original_query=query,
                sub_queries=[SubQuery(query=q, description="", source="rule")],
                is_multi_intent=False,
                method="single",
            )

        # --- 强并列模式：X以及Y / X同时Y ---
        for conj in self._STRONG_CONJUNCTIONS:
            if conj in q:
                parts = self._split_by_conjunction(q, conj)
                if self._is_valid_split(parts):
                    return self._build_rule_result(q, parts, "rule")

        # --- 中等并列模式：X和Y（需满足"多指标"特征） ---
        if self._has_multiple_metrics(q):
            for conj in self._MEDIUM_CONJUNCTIONS:
                # "和"字太常见，需要更严格的判断：
                # 前后都包含指标关键词
                parts = self._split_by_conjunction(q, conj)
                if self._both_have_metrics(parts):
                    return self._build_rule_result(q, parts, "rule")

        return None

    def _is_group_statement(self, query: str) -> bool:
        """判断是否为分组统计/列举类单意图问题"""
        import re

        for pat in self._GROUP_PATTERNS:
            if re.search(pat, query):
                return True
        return False

    def _split_by_conjunction(self, query: str, conj: str) -> list[str]:
        """按连接词拆分，补全子问题（使每个子问题可独立执行）"""
        parts = [p.strip() for p in query.split(conj) if p.strip()]
        if len(parts) < 2:
            return parts

        # 清理每部分首尾的标点
        parts = [re.sub(r"^[，,。、；;]+|[，,。、；;]+$", "", p).strip() for p in parts]

        # 提取共同的主语/实体（第一部分开头到第一个指标或动词之前）
        # 例如 "刘敏的销售金额和数量" → 主语是"刘敏的"
        subject = self._extract_subject(parts[0])

        # 补全每一部分
        completed = []
        for i, part in enumerate(parts):
            if i == 0:
                completed.append(part)
            else:
                # 如果第二部分不完整（没有主语），补上主语
                if subject and not self._has_subject(part):
                    completed.append(f"{subject}{part}")
                else:
                    completed.append(part)

        return completed

    def _extract_subject(self, first_part: str) -> str:
        """提取第一部分的主语/实体，用于补全后半句

        支持多种模式：
        1. "刘敏的销售金额" → 主语"刘敏的"
        2. "刘敏一共有多少条销售记录" → 主语"刘敏的"
        3. "技术部有多少人" → 主语"技术部的"
        """
        # 模式1：[实体]的 → 直接提取
        match = re.match(r"^(.{1,20}?的)", first_part)
        if match:
            return match.group(1)

        # 模式2：[实体]+动词 → 提取实体并加上"的"
        # 常见动词：有、一共、总、销售、负责、管理、属于
        match = re.match(r"^(.{2,15}?)(?:有|一共|总|销售|负责|管理|属于|是|的|在)", first_part)
        if match:
            entity = match.group(1).strip()
            # 过滤掉太短或太泛的
            if len(entity) >= 2:
                return f"{entity}的"

        return ""

    def _has_subject(self, part: str) -> bool:
        """判断一部分是否已有明确主语/实体（不缺）"""
        # 如果包含"的"且前面有实体内容，认为有主语
        if re.search(r"^.{1,10}的", part):
            return True
        return False

    def _is_valid_split(self, parts: list[str]) -> bool:
        """判断拆分是否有效（至少2部分，每部分都有实质内容）"""
        if len(parts) < 2:
            return False
        for p in parts:
            if len(p.strip()) < 2:
                return False
        return True

    def _has_multiple_metrics(self, query: str) -> bool:
        """判断问题中是否包含多个指标关键词"""
        found = [kw for kw in self._METRIC_KEYWORDS if kw in query]
        return len(found) >= 2

    def _both_have_metrics(self, parts: list[str]) -> bool:
        """判断拆分后的各部分是否都包含指标关键词"""
        if len(parts) < 2:
            return False
        for p in parts:
            has_any = any(kw in p for kw in self._METRIC_KEYWORDS)
            if not has_any:
                return False
        return True

    def _build_rule_result(
        self, original: str, parts: list[str], source: str
    ) -> DecompositionResult:
        """构建分解结果"""
        sub_queries = []
        for i, part in enumerate(parts):
            # 确保每个子问题是完整的问句形式
            q = self._complete_query(part)
            sub_queries.append(
                SubQuery(
                    query=q,
                    description=f"子问题{i + 1}",
                    source=source,
                )
            )
        return DecompositionResult(
            original_query=original,
            sub_queries=sub_queries,
            is_multi_intent=len(sub_queries) > 1,
            method=source,
        )

    def _complete_query(self, part: str) -> str:
        """补全子问题，使其成为完整可执行的查询"""
        # 如果已经是完整问句，直接返回
        if re.search(r"(多少|几个|哪些|是什么|哪个|谁|总计|合计|是多少|有多少|共有)", part):
            return part

        # 如果是名词短语，补充"有多少/是什么"
        # 例如 "总金额" → "总金额是多少"
        if any(kw in part for kw in self._METRIC_KEYWORDS):
            return f"{part}是多少"

        return part

    # ------------------------------------------------------------------
    # LLM 分解
    # ------------------------------------------------------------------

    async def _llm_decompose(self, query: str, llm: object) -> DecompositionResult | None:
        """使用 LLM 分解复杂问题"""
        llm_cfg = get_llm_config("decompose", settings)

        system_prompt = get_prompt("decompose_system")

        try:
            response = await llm.simple_chat(
                prompt=f"请分解以下问题：\n\n{query}",
                system_prompt=system_prompt,
                temperature=llm_cfg["temperature"],
                max_tokens=llm_cfg["max_tokens"],
            )

            logger.debug(f"LLM decomposition raw response for query '{query[:50]}...':\n{response}")

            # 解析 JSON
            result = self._parse_llm_response(response)
            if result is None:
                logger.debug(f"LLM decomposition: failed to parse response for '{query[:50]}...'")
                return None

            # 补全子查询列表
            sub_queries = []
            for item in result.get("sub_queries", []):
                q = item.get("query", "").strip()
                if q:
                    sub_queries.append(
                        SubQuery(
                            query=q,
                            description=item.get("description", ""),
                            source="llm",
                        )
                    )

            if not sub_queries:
                return None

            is_multi = result.get("is_multi_intent", len(sub_queries) > 1)

            return DecompositionResult(
                original_query=query,
                sub_queries=sub_queries,
                is_multi_intent=is_multi and len(sub_queries) > 1,
                method="llm",
            )

        except Exception as e:
            logger.warning(f"LLM decomposition error: {e}")
            raise

    def _parse_llm_response(self, response: str) -> dict | None:
        """解析 LLM 返回的 JSON"""
        import json

        # 去掉可能的 markdown 代码块标记
        text = response.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        # 提取 JSON 对象（找第一个 { 和最后一个 }）
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            json_str = text[start : end + 1]
            try:
                return json.loads(json_str)
            except json.JSONDecodeError:
                pass

        return None
