"""多轮查询状态管理：槽位状态 / 状态差异计算 / SQL 前校验。

对应"约束重置 vs 话题重置"的生产级做法：
- 槽位（Slot）：filters 维度 + group_by/metric/order_dir/limit
- 条件追踪：每个 filter 携带 source / turn / explicit / confidence
- 状态差异：由「解析 LLM + 规则兜底」产出 operation 与要移除的槽位，服务层据此更新状态
- 校验：被显式移除的槽位不得在最终查询条件中重现

operation 取值：
- new_query    全新问题（无历史可继承）
- inherit      沿用上一轮条件
- modify_query 修改部分条件
- remove_filter 显式移除某些槽位限制
- reset_query  显式忽略之前所有条件
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 语义槽位 → schema 列关键词（schema 列名为中文，用关键词做包含匹配）
# ---------------------------------------------------------------------------

SEMANTIC_SLOTS: dict[str, tuple[str, ...]] = {
    "customer": ("客户", "客商"),
    "region": ("大区", "区域", "营销公司", "特区", "办事处", "分公司"),
    "product": ("产品", "商品", "物料"),
    "date": ("日期", "时间", "年份", "年度", "要货日期", "发货日期"),
    "salesperson": ("业务员", "销售员", "销售经理", "客户经理", "销售人员"),
}

# 重置短语词典（约束重置）：命中后移除对应语义槽位
RESET_PHRASES: list[tuple[re.Pattern, tuple[str, ...]]] = [
    (
        re.compile(r"不限(制)?.{0,4}客户|所有客户|全部客户|不区分.{0,4}客户|去掉.{0,4}客户限制"),
        ("customer",),
    ),
    (
        re.compile(r"不限(制)?.{0,4}(区域|大区|地区)|所有(区域|大区)|全部(区域|大区)|全国(范围)?"),
        ("region",),
    ),
    (
        re.compile(r"不限(制)?.{0,4}(产品|商品|物料)|所有(产品|商品|物料)|全部(产品|商品|物料)"),
        ("product",),
    ),
    (
        re.compile(r"不限(制)?.{0,4}(时间|日期|年份|年度)|所有时间|全部时间|不限时间|全年"),
        ("date",),
    ),
    (
        re.compile(
            r"不限(制)?.{0,4}(业务员|销售员|销售经理|客户经理)"
            r"|所有(业务员|销售员)|全部(业务员|销售员)"
        ),
        ("salesperson",),
    ),
]

# 清空全部槽位
RESET_ALL_PATTERN = re.compile(
    r"不限(制)?(任何|所有)条件|忽略(之前|以上).{0,6}(条件|限制)|重置(条件|筛选)"
)

_VALID_OPERATIONS = {"new_query", "inherit", "modify_query", "remove_filter", "reset_query"}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class StateDelta:
    """本轮相对上一轮状态的差异（约束增量）"""

    operation: str = "inherit"
    remove_columns: tuple[str, ...] = ()
    """要移除的过滤列（语义槽位映射到 schema 列关键词）"""

    reset_all: bool = False
    """是否清空全部条件"""


# ---------------------------------------------------------------------------
# 规则工具
# ---------------------------------------------------------------------------


def slots_to_columns(slots) -> tuple[str, ...]:
    """语义槽位名 → schema 列关键词元组"""
    cols: list[str] = []
    for s in slots or []:
        key = str(s or "").strip().lower()
        if key in SEMANTIC_SLOTS:
            cols.extend(SEMANTIC_SLOTS[key])
        elif s:
            cols.append(str(s))
    return tuple(dict.fromkeys(cols))


def column_matches(column: str, keywords) -> bool:
    """列名是否命中关键词（包含匹配）"""
    col = column or ""
    return any(k and k in col for k in (keywords or ()))


def detect_reset(query: str) -> StateDelta:
    """规则兜底：从重置短语词典识别要移除的槽位"""
    if not query:
        return StateDelta(operation="inherit")
    if RESET_ALL_PATTERN.search(query):
        return StateDelta(operation="reset_query", reset_all=True)
    cols: list[str] = []
    for pattern, slots in RESET_PHRASES:
        if pattern.search(query):
            cols.extend(slots_to_columns(slots))
    cols = list(dict.fromkeys(cols))
    if cols:
        return StateDelta(operation="remove_filter", remove_columns=tuple(cols))
    return StateDelta(operation="inherit")


def strip_reset_phrases(query: str) -> str:
    """剥离"重置短语"，得到用于规划的可检索文本。

    避免重置短语里的字面维度词（如"不限制销售客户"中的"客户"）被误当成查询维度/实体。
    """
    text = query or ""
    for pattern, _ in RESET_PHRASES:
        text = pattern.sub(" ", text)
    text = RESET_ALL_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" ，,。;；、")


def apply_delta(previous_state: dict | None, delta: StateDelta) -> dict:
    """按差异更新上一轮状态，得到本轮可继承的状态

    - reset_all：清空 filters 与维度槽位
    - remove_columns：仅移除匹配列；若维度槽位被移除，则一并清掉 group_by 等
    - 其余条件保留（约束重置而非话题重置）
    """
    state = dict(previous_state or {})
    filters = [f for f in (state.get("filters") or []) if isinstance(f, dict)]

    if delta.reset_all:
        filters = []
        for k in ("group_by", "metric", "order_dir", "limit"):
            state.pop(k, None)
    elif delta.remove_columns:
        filters = [
            f for f in filters if not column_matches(str(f.get("column", "")), delta.remove_columns)
        ]
        group_by = str(state.get("group_by") or "")
        if group_by and column_matches(group_by, delta.remove_columns):
            for k in ("group_by", "metric", "order_dir", "limit"):
                state.pop(k, None)

    state["filters"] = filters
    return state


def enforce_removed_columns(filters, remove_columns) -> tuple[list, list]:
    """校验/兜底：被显式移除的列不得出现在最终条件中。

    Returns:
        (保留的 filters, 被剔除的 filters)
    """
    if not remove_columns:
        return list(filters or []), []
    kept, dropped = [], []
    for f in filters or []:
        col = getattr(f, "column", None) or (f.get("column") if isinstance(f, dict) else "")
        if column_matches(str(col or ""), remove_columns):
            dropped.append(f)
        else:
            kept.append(f)
    return kept, dropped


# ---------------------------------------------------------------------------
# 状态解析器（LLM 主导 + 规则兜底）
# ---------------------------------------------------------------------------


class QueryStateResolver:
    """多轮查询状态解析器

    在"有历史状态"时判定本轮 operation（是否换话题 / 是否显式移除槽位 / 是否重置），
    服务层据此对状态做增量更新，而不是把历史条件一股脑带上。
    """

    def __init__(self, llm=None):
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            try:
                from app.providers.llm.factory import create_llm

                self._llm = create_llm(
                    provider=getattr(settings, "rewrite_llm_provider", None)
                    or settings.llm_provider,
                    model=getattr(settings, "rewrite_llm_model", None) or None,
                )
            except Exception as e:
                logger.warning(f"Failed to create LLM for QueryStateResolver: {e}")
                self._llm = None
        return self._llm

    async def resolve(self, query: str, previous_state: dict | None) -> StateDelta:
        """计算本轮相对上一轮状态的差异"""
        if not previous_state or (
            not previous_state.get("filters") and not previous_state.get("group_by")
        ):
            # 无历史可继承 → 全新查询
            return StateDelta(operation="new_query")

        # 规则兜底（显式重置短语）优先，稳定且免一次 LLM
        rule_delta = detect_reset(query)
        if rule_delta.reset_all or rule_delta.remove_columns:
            return rule_delta

        if not getattr(settings, "chat_state_resolver_enabled", True):
            return rule_delta

        llm = self._get_llm()
        if llm is None:
            return rule_delta

        try:
            delta = await self._llm_resolve(query, previous_state, llm)
            if delta is not None:
                return delta
        except Exception as e:
            logger.warning(f"LLM state resolve failed: {e}, fallback to rule")
        return rule_delta

    async def _llm_resolve(self, query, previous_state, llm) -> StateDelta | None:
        prev_filters = [
            {"column": f.get("column"), "value": f.get("value")}
            for f in (previous_state.get("filters") or [])
            if isinstance(f, dict)
        ]
        prev_desc = {
            "filters": prev_filters,
            "group_by": previous_state.get("group_by") or "",
            "metric": previous_state.get("metric") or "",
            "limit": previous_state.get("limit") or 0,
        }

        system_prompt = """你是一个多轮查询状态解析器。给定"上一轮查询状态"和"用户最新问题"，
判断本轮相对上一轮的操作类型（operation），并列出需要移除的语义槽位。

operation 取值：
- inherit       沿用上一轮全部条件（含隐含继承），只是换个说法/补充信息
- modify_query  修改部分条件（如换排序、换分组、改数量）
- remove_filter 用户显式取消某些限制（如"不限制客户""所有客户""不限区域"）
- reset_query   用户显式要求忽略之前所有条件（如"忽略之前的条件，重新统计"）
- new_query     与上一轮无关的全新问题

remove_slots 取值（仅在 operation=remove_filter/reset_query 时给出）：
customer / region / product / date / salesperson

【重要】"不限制销售客户""不限客户""所有客户" → remove_filter + remove_slots=["customer"]，
不要判定为 new_query。

仅输出 JSON，格式：
{"operation": "...", "remove_slots": ["..."]}"""

        prompt = (
            f"上一轮查询状态：\n{json.dumps(prev_desc, ensure_ascii=False)}\n\n"
            f"用户最新问题：{query}\n\n请输出 JSON："
        )

        raw = await llm.simple_chat(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.0,
            # 推理模型思考会占用 max_tokens，留足预算避免 JSON 为空/截断
            max_tokens=1200,
        )
        data = _loads_json(raw)
        if not isinstance(data, dict):
            return None

        op = str(data.get("operation") or "").strip().lower()
        if op not in _VALID_OPERATIONS:
            return None
        if op == "reset_query":
            return StateDelta(operation="reset_query", reset_all=True)
        if op == "remove_filter":
            slots = data.get("remove_slots") or []
            if not isinstance(slots, list):
                slots = []
            cols = slots_to_columns(slots)
            if cols:
                return StateDelta(operation="remove_filter", remove_columns=cols)
            # 给出了 remove_filter 但没给槽位 → 退回规则
            return detect_reset(query)
        return StateDelta(operation=op)


def _loads_json(text: str | None) -> dict | None:
    """从 LLM 输出中尽力解析 JSON 对象"""
    if not text:
        return None
    s = text.strip()
    if s.startswith("```"):
        s = re.sub(r"^```(?:json)?\s*", "", s)
        s = re.sub(r"\s*```$", "", s)
    start, end = s.find("{"), s.rfind("}")
    if start >= 0 and end > start:
        s = s[start : end + 1]
    try:
        data = json.loads(s)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None
