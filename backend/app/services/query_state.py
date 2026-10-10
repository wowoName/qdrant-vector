"""多轮查询状态管理：槽位状态 / 状态差异计算 / SQL 前校验。

对应“约束重置 vs 话题重置”的生产级做法：

- 槽位：filters 维度 + group_by / metric / order_dir / limit
- 条件追踪：每个 filter 可携带 source / turn / explicit / confidence
- 状态差异：解析 LLM + 规则兜底，产出 operation 与待移除槽位
- 校验：被显式移除的槽位不得在最终查询条件中重现

operation 取值：
- new_query：全新问题
- inherit：沿用上一轮条件
- modify_query：修改部分条件
- remove_filter：显式移除某些槽位限制
- reset_query：显式重置查询条件
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 语义槽位 → schema 列关键词
# 注意：这里采用包含匹配，关键词应尽量贴合真实数据库字段
# ---------------------------------------------------------------------------

SEMANTIC_SLOTS: dict[str, tuple[str, ...]] = {
    "customer": (
        "客户",
        "客商",
        "客户名称",
        "客户单位",
        "购货单位",
        "购货客户",
        "往来单位",
        "往来客户",
        "客户编码",
        "客户编号",
        "客户类别",
        "客户类型",
        "终端客户",
        "经销商",
    ),
    "region": (
        "大区",
        "区域",
        "营销公司",
        "特区",
        "办事处",
        "分公司",
        "省份",
        "省市",
        "城市",
        "地区",
        "销售区域",
        "销售片区",
        "所属区域",
        "组织区域",
        "销售组织",
        "经营区域",
    ),
    "product": (
        "产品",
        "商品",
        "物料",
        "产品名称",
        "商品名称",
        "物料名称",
        "品名",
        "产品型号",
        "商品型号",
        "物料编码",
        "产品编码",
        "产品类别",
        "商品类别",
        "产品分类",
        "SKU",
    ),
    "date": (
        "日期",
        "时间",
        "年份",
        "年度",
        "要货日期",
        "发货日期",
        "月份",
        "季度",
        "统计周期",
        "时间范围",
        "时间段",
        "起始日期",
        "结束日期",
        "创建时间",
        "下单时间",
        "订单日期",
        "销售日期",
        "开票日期",
        "出库日期",
    ),
    "salesperson": (
        "业务员",
        "销售员",
        "销售经理",
        "客户经理",
        "销售人员",
        "销售代表",
        "业务负责人",
        "销售负责人",
        "跟单员",
        "负责人",
        "业务人员",
        "经办人",
        "所属业务员",
    ),
}


# ---------------------------------------------------------------------------
# 约束重置短语：命中后只移除指定语义槽位
# ---------------------------------------------------------------------------

RESET_PHRASES: list[tuple[re.Pattern, tuple[str, ...]]] = [
    # 客户
    (
        re.compile(
            r"不限(?:制)?.{0,4}客户"
            r"|所有客户"
            r"|全部客户"
            r"|不区分.{0,4}客户"
            r"|去掉.{0,4}客户限制"
            r"|(?:取消|清除|移除|删除|解除).{0,6}"
            r"(?:客户|客商)(?:筛选|限制|条件|范围)"
            r"|(?:客户|客商)(?:范围)?(?:不限|不设限|不做限制|不再限定)"
            r"|不按(?:客户|客商)(?:筛选|过滤|限制)"
            r"|(?:客户|客商)(?:维度)?(?:不参与|不作为)(?:筛选|过滤|限制)"
            r"|(?:客户|客商)(?:范围)?(?:全部放开|完全放开)"
            r"|(?:客户|客商)(?:筛选条件)?(?:全部取消|全部清除)"
        ),
        ("customer",),
    ),
    # 区域
    (
        re.compile(
            r"不限(?:制)?.{0,4}(?:区域|大区|地区)"
            r"|所有(?:区域|大区|地区)"
            r"|全部(?:区域|大区|地区)"
            r"|全国(?:范围)?"
            r"|(?:取消|清除|移除|删除|解除).{0,6}"
            r"(?:区域|大区|地区|省份|城市|销售片区|销售区域)"
            r"(?:筛选|限制|条件)"
            r"|(?:区域|大区|地区|省份|城市|销售片区|销售区域)"
            r"(?:范围)?(?:不限|不设限|不做限制|不再限定)"
            r"|不按(?:区域|大区|地区|省份|城市)(?:筛选|过滤|限制)"
            r"|(?:全国范围|全国所有区域|全区域)"
        ),
        ("region",),
    ),
    # 产品
    (
        re.compile(
            r"不限(?:制)?.{0,4}(?:产品|商品|物料)"
            r"|所有(?:产品|商品|物料)"
            r"|全部(?:产品|商品|物料)"
            r"|(?:取消|清除|移除|删除|解除).{0,6}"
            r"(?:产品|商品|物料|品名|SKU)(?:筛选|限制|条件|范围)"
            r"|(?:产品|商品|物料|SKU)(?:范围)?"
            r"(?:不限|不设限|不做限制|不再限定)"
            r"|不按(?:产品|商品|物料|品名|SKU)(?:筛选|过滤|限制)"
        ),
        ("product",),
    ),
    # 时间
    (
        re.compile(
            r"不限(?:制)?.{0,4}(?:时间|日期|年份|年度)"
            r"|所有时间"
            r"|全部时间"
            r"|不限时间"
            r"|全年"
            r"|(?:取消|清除|移除|删除|解除).{0,6}"
            r"(?:时间|日期|年份|年度|月份|季度|统计周期|时间范围)"
            r"(?:筛选|限制|条件)"
            r"|(?:时间|日期|年份|年度|月份|季度|统计周期)"
            r"(?:范围)?(?:不限|不设限|不做限制|不再限定)"
            r"|不按(?:时间|日期|年份|年度|月份|季度)(?:筛选|过滤|限制)"
        ),
        ("date",),
    ),
    # 销售人员
    (
        re.compile(
            r"不限(?:制)?.{0,4}(?:业务员|销售员|销售经理|客户经理)"
            r"|所有(?:业务员|销售员|销售经理|客户经理|销售人员|销售代表)"
            r"|全部(?:业务员|销售员|销售经理|客户经理|销售人员|销售代表)"
            r"|(?:取消|清除|移除|删除|解除).{0,6}"
            r"(?:业务员|销售员|销售经理|客户经理|销售人员|销售代表|跟单员)"
            r"(?:筛选|限制|条件|范围)"
            r"|(?:业务员|销售员|销售经理|客户经理|销售人员|销售代表|跟单员)"
            r"(?:范围)?(?:不限|不设限|不做限制|不再限定)"
            r"|不按(?:业务员|销售员|销售经理|客户经理|销售人员|销售代表|跟单员)"
            r"(?:筛选|过滤|限制)"
        ),
        ("salesperson",),
    ),
]


# ---------------------------------------------------------------------------
# 全局重置：清除本轮查询继承的全部业务槽位
# 注意：不能清除后端强制施加的权限、租户隔离等安全约束
# ---------------------------------------------------------------------------

RESET_ALL_PATTERN = re.compile(
    r"不限(?:制)?(?:任何|所有)条件"
    r"|忽略(?:之前|以上).{0,6}(?:条件|限制)"
    r"|重置(?:条件|筛选)"
    r"|重新开始(?:查询|统计|分析)?"
    r"|从头(?:开始)?(?:查询|统计|分析)"
    r"|清空(?:全部|所有)?(?:查询)?(?:条件|筛选|槽位)"
    r"|清除(?:全部|所有)(?:筛选|限制|条件)"
    r"|不要沿用(?:之前|上轮|上一轮|历史)(?:的)?(?:查询|条件|筛选)"
    r"|不再沿用(?:之前|上轮|上一轮|历史)(?:的)?(?:查询|条件|筛选)"
    r"|忽略(?:之前|上轮|上一轮|历史)(?:的)?(?:查询|筛选|限制|条件)"
    r"|不考虑(?:之前|上轮|上一轮)(?:的)?(?:筛选|限制|条件)"
    r"|重新做一遍(?:查询|统计|分析)"
    r"|重新统计(?:全部|所有)?数据"
)


_VALID_OPERATIONS = {
    "new_query",
    "inherit",
    "modify_query",
    "remove_filter",
    "reset_query",
}


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------


@dataclass
class StateDelta:
    """本轮相对上一轮状态的差异。"""

    operation: str = "inherit"
    remove_columns: tuple[str, ...] = ()
    reset_all: bool = False


# ---------------------------------------------------------------------------
# 规则工具
# ---------------------------------------------------------------------------


def slots_to_columns(slots) -> tuple[str, ...]:
    """语义槽位名 → schema 列关键词元组。"""
    cols: list[str] = []

    for slot in slots or []:
        key = str(slot or "").strip().lower()

        if key in SEMANTIC_SLOTS:
            cols.extend(SEMANTIC_SLOTS[key])
        elif slot:
            cols.append(str(slot))

    return tuple(dict.fromkeys(cols))


def column_matches(column: str, keywords) -> bool:
    """列名是否命中关键词（包含匹配）。"""
    col = column or ""
    return any(keyword and keyword in col for keyword in (keywords or ()))


def detect_reset(query: str) -> StateDelta:
    """规则兜底：从重置短语词典识别要移除的槽位。"""
    if not query:
        return StateDelta(operation="inherit")

    if RESET_ALL_PATTERN.search(query):
        return StateDelta(
            operation="reset_query",
            reset_all=True,
        )

    cols: list[str] = []

    for pattern, slots in RESET_PHRASES:
        if pattern.search(query):
            cols.extend(slots_to_columns(slots))

    cols = list(dict.fromkeys(cols))

    if cols:
        return StateDelta(
            operation="remove_filter",
            remove_columns=tuple(cols),
        )

    return StateDelta(operation="inherit")


def strip_reset_phrases(query: str) -> str:
    """剥离重置短语，得到用于查询规划的文本。"""
    text = query or ""

    for pattern, _ in RESET_PHRASES:
        text = pattern.sub(" ", text)

    text = RESET_ALL_PATTERN.sub(" ", text)
    text = re.sub(r"\s+", " ", text)

    return text.strip(" ，,。;；、")


def apply_delta(
    previous_state: dict | None,
    delta: StateDelta,
) -> dict:
    """按差异更新上一轮状态，得到本轮可继承的状态。

    - reset_all：清空 filters 与维度槽位
    - remove_columns：仅移除匹配列
    - 其余条件保留
    """
    state = dict(previous_state or {})

    filters = [item for item in (state.get("filters") or []) if isinstance(item, dict)]

    if delta.reset_all:
        filters = []

        for key in (
            "group_by",
            "metric",
            "order_dir",
            "limit",
        ):
            state.pop(key, None)

    elif delta.remove_columns:
        filters = [
            item
            for item in filters
            if not column_matches(
                str(item.get("column", "")),
                delta.remove_columns,
            )
        ]

        group_by = str(state.get("group_by") or "")

        if group_by and column_matches(
            group_by,
            delta.remove_columns,
        ):
            for key in (
                "group_by",
                "metric",
                "order_dir",
                "limit",
            ):
                state.pop(key, None)

    state["filters"] = filters

    return state


def enforce_removed_columns(
    filters,
    remove_columns,
) -> tuple[list, list]:
    """校验被显式移除的列不得出现在最终过滤条件中。

    Returns:
        (保留的 filters, 被剔除的 filters)
    """
    if not remove_columns:
        return list(filters or []), []

    kept = []
    dropped = []

    for item in filters or []:
        if isinstance(item, dict):
            column = item.get("column", "")
        else:
            column = getattr(item, "column", "")

        if column_matches(
            str(column or ""),
            remove_columns,
        ):
            dropped.append(item)
        else:
            kept.append(item)

    return kept, dropped


# ---------------------------------------------------------------------------
# 状态解析器：LLM 主导 + 规则兜底
# ---------------------------------------------------------------------------


class QueryStateResolver:
    """多轮查询状态解析器。

    有历史状态时，判定本轮 operation，并确定需要移除的槽位。
    """

    def __init__(self, llm=None):
        self._llm = llm

    def _get_llm(self):
        if self._llm is None:
            try:
                from app.providers.llm.factory import create_llm

                self._llm = create_llm(
                    provider=(
                        getattr(
                            settings,
                            "rewrite_llm_provider",
                            None,
                        )
                        or settings.llm_provider
                    ),
                    model=(
                        getattr(
                            settings,
                            "rewrite_llm_model",
                            None,
                        )
                        or None
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to create LLM for QueryStateResolver: %s",
                    exc,
                )
                self._llm = None

        return self._llm

    async def resolve(
        self,
        query: str,
        previous_state: dict | None,
    ) -> StateDelta:
        """计算本轮相对上一轮状态的差异。"""
        if not previous_state or (
            not previous_state.get("filters") and not previous_state.get("group_by")
        ):
            return StateDelta(operation="new_query")

        # 显式重置规则优先，避免额外 LLM 调用
        rule_delta = detect_reset(query)

        if rule_delta.reset_all or rule_delta.remove_columns:
            return rule_delta

        if not getattr(
            settings,
            "chat_state_resolver_enabled",
            True,
        ):
            return rule_delta

        llm = self._get_llm()

        if llm is None:
            return rule_delta

        try:
            delta = await self._llm_resolve(
                query,
                previous_state,
                llm,
            )

            if delta is not None:
                return delta

        except Exception as exc:
            logger.warning(
                "LLM state resolve failed: %s, fallback to rule",
                exc,
            )

        return rule_delta

    async def _llm_resolve(
        self,
        query,
        previous_state,
        llm,
    ) -> StateDelta | None:
        """调用 LLM 判断本轮查询状态变化。"""
        prev_filters = [
            {
                "column": item.get("column"),
                "value": item.get("value"),
            }
            for item in (previous_state.get("filters") or [])
            if isinstance(item, dict)
        ]

        prev_desc = {
            "filters": prev_filters,
            "group_by": previous_state.get("group_by") or "",
            "metric": previous_state.get("metric") or "",
            "order_dir": previous_state.get("order_dir") or "",
            "limit": previous_state.get("limit") or 0,
        }

        system_prompt = """你是一个多轮查询状态解析器。

根据上一轮查询状态和用户最新问题，判断本轮相对上一轮的操作类型。

operation 取值：
- inherit：沿用上一轮全部条件
- modify_query：修改部分条件，例如换排序、换分组、改数量
- remove_filter：用户明确取消某些筛选条件
- reset_query：用户明确要求忽略之前全部查询条件
- new_query：与上一轮无关的新问题

remove_slots 取值：
customer / region / product / date / salesperson

判断原则：
1. “不限制销售客户”“不限客户”“所有客户”
   应判断为 remove_filter，remove_slots=["customer"]。
2. 仅修改某个条件时，不应清除其他仍然有效的条件。
3. “忽略之前所有筛选条件”等明确表达才属于 reset_query。
4. 不得将“更换客户”“查询另一个客户”误判为取消客户筛选。
5. 不得将“客户经理排名”误判为取消客户限制。
6. 如果用户问题明显与上一轮无关，判断为 new_query。
7. 如果没有明确证据表明条件被取消，不要擅自删除条件。

仅输出 JSON：
{"operation": "...", "remove_slots": []}
"""

        prompt = (
            f"上一轮查询状态：\n"
            f"{json.dumps(prev_desc, ensure_ascii=False)}\n\n"
            f"用户最新问题：\n{query}\n\n"
            f"请输出 JSON："
        )

        raw = await llm.simple_chat(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.0,
            max_tokens=1200,
        )

        data = _loads_json(raw)

        if not isinstance(data, dict):
            return None

        operation = str(data.get("operation") or "").strip().lower()

        if operation not in _VALID_OPERATIONS:
            return None

        if operation == "reset_query":
            return StateDelta(
                operation="reset_query",
                reset_all=True,
            )

        if operation == "remove_filter":
            slots = data.get("remove_slots") or []

            if not isinstance(slots, list):
                slots = []

            columns = slots_to_columns(slots)

            if columns:
                return StateDelta(
                    operation="remove_filter",
                    remove_columns=columns,
                )

            # 没有提供有效槽位时，退回规则
            return detect_reset(query)

        return StateDelta(operation=operation)


def _loads_json(text: str | None) -> dict | None:
    """从 LLM 输出中尽力解析 JSON 对象。"""
    if not text:
        return None

    content = text.strip()

    if content.startswith("```"):
        content = re.sub(
            r"^```(?:json)?\s*",
            "",
            content,
        )
        content = re.sub(
            r"\s*```$",
            "",
            content,
        )

    start = content.find("{")
    end = content.rfind("}")

    if start >= 0 and end > start:
        content = content[start : end + 1]

    try:
        data = json.loads(content)
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None
