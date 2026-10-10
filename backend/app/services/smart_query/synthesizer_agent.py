"""
SynthesizerAgent - 结果融合代理

负责：
1. 接收 SQL 查询结果 + 向量检索结果
2. 判断各结果的可信度和完整性
3. 融合多路结果，生成最终回答
4. 输出引用来源

设计原则：
- SQL 结果优先级高于向量检索（精确 > 模糊）
- 混合模式下以 SQL 为主，向量检索做补充解释
- 始终输出数据来源（哪个文件、哪个 Sheet、执行了什么 SQL）
- 失败时给出明确的错误说明，不编造数据
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Optional

from app.core.logging import get_logger
from app.providers.llm.base import ChatMessage
from app.providers.llm.factory import create_llm
from .types import (
    FilterInfo,
    MultiRetrievalResult,
    QueryIntent,
    QueryOperation,
    QueryPlan,
    RetrievalHit,
    RetrievalSource,
    SQLQueryResult,
    RetrievalQueryResult,
    AgentContext,
)
logger = get_logger(__name__)


SYNTHESIS_SYSTEM_PROMPT = """你是一个专业的数据回答助手。请根据提供的查询结果，生成准确、清晰、有条理的回答。

重要规则：
1. 只使用提供的数据回答问题，不要编造或推断数据之外的信息
2. 如果提供了 SQL 查询结果，优先使用 SQL 的精确数据（SQL 是完整的精确查询，不是模糊检索）
3. 如果提供了向量检索结果，可以用它来补充说明或上下文（向量检索是近似匹配，可能不完整）
4. 回答时请条理清晰，重点突出
5. 对于统计数字类问题，如果同时提供了总数和明细列表，请先给出明确的数字答案，再列出明细
6. 对于列表类问题，使用有序或无序列表呈现；如果有总数和展示数的差异，要说明"共 X 条，展示前 Y 条"
7. 不要提"根据SQL查询结果"或"根据向量检索"这类技术性表述
8. 如果数据不足或查询失败，明确说明"根据现有资料无法回答该问题"
9. 当用户问"有多少个/多少名"时，默认同时给出总数和明细列表，明细是重要的参考信息

请用自然语言组织答案，让用户容易理解。"""


class SynthesizerAgent:
    """
    结果融合代理

    将 SQL 结果和向量检索结果融合，生成最终的自然语言回答。
    """

    def __init__(self, use_llm_for_synthesis: bool = True):
        self.use_llm_for_synthesis = use_llm_for_synthesis
        self._llm = None
        # 流式增量回调：绑定后，合成阶段的 LLM 调用会逐段把文本透传出去
        self._delta_sink: Optional[Callable[[str], None]] = None
        # 对话历史块：绑定后统一注入合成 prompt，用于多轮上下文理解
        self._history_text: str = ""

    def set_delta_sink(self, sink: Optional[Callable[[str], None]]) -> None:
        """绑定 / 解绑流式增量回调（绑定后 LLM 合成走真流式）

        仅影响文本产出的方式，不改变任何检索逻辑与最终答案内容：
        累积所有增量后返回的字符串，与原先 simple_chat 的返回值一致。
        """
        self._delta_sink = sink

    def set_history_text(self, history_text: Optional[str]) -> None:
        """绑定 / 解绑对话历史块（已由 ContextWindowManager 完成压缩、蒸馏、丢弃）

        仅在合成 prompt 中追加一段「最近对话」，用于多轮上下文理解；
        不参与检索，也不改变检索结果。
        """
        self._history_text = history_text or ""

    def _inject_history(self, system_prompt: Optional[str]) -> Optional[str]:
        """把对话历史块追加到 system prompt（统一注入点，覆盖全部合成分支）"""
        if not self._history_text:
            return system_prompt
        block = (
            "\n\n【最近对话】（仅用于理解上下文指代与追问意图，"
            "不要把这些内容当作本次回答的事实依据）\n"
            f"{self._history_text}"
        )
        return (system_prompt or "") + block

    async def _llm_text(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """调用 LLM 生成文本。

        - 未绑定 delta_sink：与 BaseLLM.simple_chat 行为完全一致（非流式）
        - 已绑定 delta_sink：走 chat_stream，逐段回调，最后返回拼接结果
        """
        llm = self._get_llm()
        if llm is None:
            raise RuntimeError("LLM 不可用")

        # 统一注入对话历史（多轮上下文），覆盖所有合成分支
        system_prompt = self._inject_history(system_prompt)

        sink = self._delta_sink
        if sink is None:
            text = await llm.simple_chat(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            # 与 BaseLLM.simple_chat 完全一致的 messages 构造，保证输入相同
            messages: list[ChatMessage] = []
            if system_prompt:
                messages.append(ChatMessage("system", system_prompt))
            messages.append(ChatMessage("user", prompt))

            parts: list[str] = []
            async for chunk in llm.chat_stream(
                messages, temperature=temperature, max_tokens=max_tokens
            ):
                if chunk.get("type") != "content":
                    continue
                piece = chunk.get("content", "")
                if piece:
                    parts.append(piece)
                    sink(piece)
            text = "".join(parts)

        if text and text.strip():
            return text

        # 合成模型偶发返回空串（模型抖动）：重试一次，避免出现空白答案
        logger.warning("LLM synthesis returned empty, retrying once")
        try:
            retry = await llm.simple_chat(
                prompt=prompt,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except Exception as e:
            logger.warning(f"LLM synthesis retry failed: {e}")
            return text
        return retry or text

    def _get_llm(self):
        """获取 LLM 实例（懒加载）"""
        if self._llm is None:
            try:
                from app.core.config import settings

                self._llm = create_llm(
                    provider=settings.llm_provider,
                    model=settings.chat_llm_model or None,
                )
            except Exception as e:
                logger.warning(f"Failed to create LLM for SynthesizerAgent: {e}")
                self._llm = None
        return self._llm

    # ------------------------------------------------------------------
    # 主入口（多路并行新接口）
    # ------------------------------------------------------------------

    async def synthesize_multi_subquery(
        self,
        query: str,
        sub_queries: list[str],
        sql_hits: list = None,
        vector_hits: list = None,
    ) -> str:
        """
        多子查询结果合成：多个子查询的结果合并后，生成完整回答

        Args:
            query: 原始用户问题
            sub_queries: 子查询列表
            sql_hits: 所有子查询的 SQL 命中结果（hit.payload.sub_query 标记来源）
            vector_hits: 所有子查询的向量检索命中结果

        Returns:
            合成后的回答文本
        """
        sql_hits = sql_hits or []
        vector_hits = vector_hits or []

        # 没有任何结果
        if not sql_hits and not vector_hits:
            return "未找到相关信息，无法回答您的问题。"

        llm = self._get_llm()
        if llm is None:
            # LLM 不可用时，降级为拼接展示
            answer = self._fallback_subquery_answer(query, sub_queries, sql_hits, vector_hits)
            return self._append_detail_download_link(answer, sql_hits)

        # 构建上下文：按子查询分组展示结果
        context_text = self._build_subquery_context(sub_queries, sql_hits, vector_hits)

        system_prompt = (
            "你是一个专业的数据分析助手。请根据以下检索结果，回答用户的问题。\n\n"
            "【回答要求】\n"
            "1. 基于检索到的数据回答，不要编造数据\n"
            "2. 全面回答用户问题的每个部分，不要遗漏\n"
            "3. 用自然、简洁的语言组织答案，采用列表或表格形式呈现更清晰\n"
            "4. 数值类数据要准确，带单位\n"
            "5. 如果某个子问题没有找到数据，明确说明「未找到相关数据」，并简单解释可能的原因\n"
            "6. 回答结构清晰：先给出核心结论，再展开细节，最后说明数据来源\n"
            "7. 如果同一个信息在多个来源中都有，优先使用最匹配的数据源\n"
            "8. **重要：列表类数据（如人员名单、部门列表、客户名称等）必须全量列出，不要只展示前几个，不要用「等」字省略**\n"
            "9. 如果数据量很大（超过50条），可以分组展示，但必须确保每条数据都呈现给用户\n\n"
            f"【检索结果】\n{context_text}"
        )

        try:
            answer = await self._llm_text(
                prompt=f"用户问题：{query}",
                system_prompt=system_prompt,
                temperature=0.3,
                max_tokens=1500,
            )
            answer = answer.strip()
        except Exception as e:
            logger.error(f"Multi-subquery synthesis failed: {e}")
            answer = self._fallback_subquery_answer(query, sub_queries, sql_hits, vector_hits)

        # 明细超过内联上限时，附上完整明细下载链接
        return self._append_detail_download_link(answer, sql_hits)

    def _build_subquery_context(
        self, sub_queries: list[str], sql_hits: list, vector_hits: list
    ) -> str:
        """按子查询分组构建上下文文本"""
        from collections import defaultdict

        # 按 sub_query 分组
        sql_by_sub = defaultdict(list)
        for hit in sql_hits:
            sub_q = hit.payload.get("sub_query", "未知")
            sql_by_sub[sub_q].append(hit)

        vec_by_sub = defaultdict(list)
        for hit in vector_hits:
            sub_q = getattr(hit, "payload", {}).get("sub_query", "未知") if hasattr(hit, "payload") else "未知"
            vec_by_sub[sub_q].append(hit)

        lines = []
        for i, sub_q in enumerate(sub_queries):
            lines.append(f"\n【子问题 {i+1}】{sub_q}")

            # SQL 结果
            sub_sql = sql_by_sub.get(sub_q, [])
            if sub_sql:
                lines.append("  [SQL 检索结果]")
                # 分离聚合、去重列表、明细
                agg_hits = [h for h in sub_sql if h.payload.get("type") == "aggregate"]
                distinct_hits = [h for h in sub_sql if h.payload.get("type") == "distinct_list"]
                detail_hits = [h for h in sub_sql if h.payload.get("type") not in ("aggregate", "distinct_list")]

                for hit in agg_hits:
                    metric = hit.payload.get("target_column_alias") or hit.payload.get("target_column", "聚合值")
                    value = hit.payload.get("value", "")
                    unit = hit.payload.get("unit", "")
                    count = hit.payload.get("total_count", "")
                    source = hit.payload.get("source_file", "")
                    op = hit.payload.get("operation", "")
                    op_label = {"sum": "合计", "avg": "平均", "count": "数量"}.get(op, op)
                    lines.append(f"    • {op_label}{metric}：{value}{unit}（共{count}条记录，来源：{source}）")

                if distinct_hits:
                    # 去重列表：不超过上限时全量展示，超限展示前 N 条
                    valid_distinct = [h for h in distinct_hits if not (h.payload and h.payload.get("empty"))]
                    total_distinct = len(valid_distinct)
                    lines.append(f"    去重列表（共 {total_distinct} 个）：")
                    show_n = self.DETAIL_INLINE_LIMIT
                    for j, hit in enumerate(valid_distinct[:show_n], 1):
                        content = hit.content.strip() if hit.content else str(hit.payload.get("value", ""))
                        lines.append(f"      {j}. {content}")
                    if total_distinct > show_n:
                        lines.append(f"      （其余 {total_distinct - show_n} 个见回答末尾的下载文件）")

                if detail_hits:
                    # 普通明细：增加到 20 条
                    show_count = min(20, len(detail_hits))
                    lines.append(f"    相关记录（共{len(detail_hits)}条，展示前{show_count}条）：")
                    for j, hit in enumerate(detail_hits[:show_count]):
                        content = hit.content[:150] if hasattr(hit, "content") and hit.content else str(hit.payload.get("row_data", ""))[:150]
                        lines.append(f"      {j+1}. {content}")
                    if len(detail_hits) > show_count:
                        lines.append(f"      ... 还有 {len(detail_hits) - show_count} 条")

            # 向量结果
            sub_vec = vec_by_sub.get(sub_q, [])
            if sub_vec:
                lines.append(f"  [向量检索结果] {len(sub_vec)}条")
                for j, hit in enumerate(sub_vec[:5]):
                    content = hit.content[:100] if hasattr(hit, "content") and hit.content else str(hit)[:100]
                    lines.append(f"    {j+1}. {content}")

            if not sub_sql and not sub_vec:
                lines.append("  （未找到相关结果）")

        return "\n".join(lines)

    def _fallback_subquery_answer(
        self, query: str, sub_queries: list[str], sql_hits: list, vector_hits: list
    ) -> str:
        """LLM 不可用时的降级回答（简单拼接）"""
        from collections import defaultdict

        sql_by_sub = defaultdict(list)
        for hit in sql_hits:
            sub_q = hit.payload.get("sub_query", "未知")
            sql_by_sub[sub_q].append(hit)

        parts = []
        for sub_q in sub_queries:
            sub_sql = sql_by_sub.get(sub_q, [])
            agg_hits = [h for h in sub_sql if h.payload.get("type") == "aggregate"]
            distinct_hits = [h for h in sub_sql if h.payload.get("type") == "distinct_list"]
            detail_hits = [h for h in sub_sql if h.payload.get("type") not in ("aggregate", "distinct_list")]

            if agg_hits:
                for hit in agg_hits:
                    metric = hit.payload.get("target_column_alias") or hit.payload.get("target_column", "值")
                    value = hit.payload.get("value", "")
                    unit = hit.payload.get("unit", "")
                    op = hit.payload.get("operation", "")
                    op_label = {"sum": "合计", "avg": "平均", "count": "数量"}.get(op, "")
                    source = hit.payload.get("source_file", "")
                    parts.append(f"{op_label}{metric}：{value}{unit}（来源：{source}）")
            elif distinct_hits:
                # 去重列表：全量展示
                valid_distinct = [h for h in distinct_hits if not (h.payload and h.payload.get("empty"))]
                total_distinct = len(valid_distinct)
                col_name = (distinct_hits[0].payload.get("column_alias")
                            or distinct_hits[0].payload.get("column", "结果"))
                parts.append(f"【{sub_q}】共 {total_distinct} 个{col_name}，全部列表：")
                for j, hit in enumerate(valid_distinct, 1):
                    content = hit.content.strip() if hit.content else str(hit.payload.get("value", ""))
                    parts.append(f"  {j}. {content}")
            elif detail_hits:
                # 明细类：展示前 20 条关键信息
                parts.append(f"【{sub_q}】找到 {len(detail_hits)} 条相关记录：")
                for j, hit in enumerate(detail_hits[:20]):
                    row_data = hit.payload.get("row_data", {}) or {}
                    # 展示几个关键字段
                    key_fields = []
                    for k, v in row_data.items():
                        if v and k not in ("type", "operation", "source_table", "sub_query", "document_id", "total_count", "source_file"):
                            key_fields.append(f"{k}: {v}")
                            if len(key_fields) >= 4:
                                break
                    if key_fields:
                        parts.append(f"  {j+1}. " + "，".join(key_fields))
                if len(detail_hits) > 20:
                    parts.append(f"  ... 还有 {len(detail_hits) - 20} 条")
            else:
                # 没有找到该子查询的结果
                parts.append(f"【{sub_q}】未找到相关数据")

        if parts:
            return "根据检索结果：\n" + "\n".join(f"• {p}" if not p.startswith("  ") else p for p in parts)
        return "未找到相关信息。"

    # ------------------------------------------------------------------
    # 明细导出下载链接
    # ------------------------------------------------------------------

    # 明细内联展示上限：超过此数量时展示前 N 条并附完整明细下载链接
    DETAIL_INLINE_LIMIT = 30

    # payload 中的内部键（导出明细时排除）
    _INTERNAL_PAYLOAD_KEYS = {
        "type", "operation", "source_table", "sub_query", "source_file",
        "document_id", "total_count", "_total_count", "_shown_count",
        "empty", "unit", "row_data",
    }

    def _collect_detail_hits(self, sql_hits) -> list:
        """从 SQL 命中中收集完整行明细 hits（排除聚合/去重列表/空标记）"""
        detail_hits = []
        for h in (sql_hits or []):
            payload = getattr(h, "payload", None) or {}
            if payload.get("type") in ("aggregate", "distinct_list"):
                continue
            if payload.get("empty"):
                continue
            detail_hits.append(h)
        return detail_hits

    def _collect_distinct_hits(self, sql_hits) -> list:
        """从 SQL 命中中收集去重列表 hits（排除空标记 / 聚合附带的度量值列表）"""
        distinct_hits = []
        for h in (sql_hits or []):
            payload = getattr(h, "payload", None) or {}
            if payload.get("type") != "distinct_list":
                continue
            if payload.get("empty"):
                continue
            # 聚合查询（SUM/AVG）附带的"去重度量值"列表无展示/下载价值（如 47 个销售金额），
            # 仅用于内部校验，不进入下载链接导出
            if payload.get("from_aggregate_query"):
                continue
            distinct_hits.append(h)
        return distinct_hits

    def _hits_to_export_rows(self, detail_hits: list) -> tuple[list, list]:
        """将明细 hits 转为 (columns, rows) 用于导出"""
        columns: list = []
        for h in detail_hits:
            for k in (h.payload or {}).keys():
                if k not in self._INTERNAL_PAYLOAD_KEYS and k not in columns:
                    columns.append(k)
        if not columns:
            columns = ["内容"]

        rows = []
        for h in detail_hits:
            payload = h.payload or {}
            if columns == ["内容"]:
                rows.append([h.content or ""])
            else:
                rows.append([payload.get(c, "") for c in columns])
        return columns, rows

    def _append_detail_download_link(self, answer: str, sql_hits) -> str:
        """明细超过内联上限时，导出 Excel 并在回答末尾附上下载链接

        导出内容（多 sheet）：
        - 完整行明细（如销售记录列表）
        - 超过上限的去重列表（如 45 个销售单号、几百个客户名）
        """
        try:
            detail_hits = self._collect_detail_hits(sql_hits)
            distinct_hits = self._collect_distinct_hits(sql_hits)

            # 判断哪些去重列表超限（按列名分组）
            from collections import defaultdict

            distinct_groups = defaultdict(list)
            for h in distinct_hits:
                payload = h.payload or {}
                col = (
                    payload.get("column_alias")
                    or payload.get("column")
                    or "列表"
                )
                val = h.content.strip() if h.content else str(payload.get("value", ""))
                if val:
                    distinct_groups[col].append(val)

            has_detail_overflow = len(detail_hits) > self.DETAIL_INLINE_LIMIT
            overflow_distinct = {
                col: vals
                for col, vals in distinct_groups.items()
                if len(vals) > self.DETAIL_INLINE_LIMIT
            }

            if not has_detail_overflow and not overflow_distinct:
                return answer

            # 组装导出 sheets
            from .detail_export import export_sheets_to_excel

            sheets = []
            if has_detail_overflow:
                columns, rows = self._hits_to_export_rows(detail_hits)
                sheets.append(("明细数据", columns, rows))
            for col, vals in overflow_distinct.items():
                # 去重保序
                seen, unique_vals = set(), []
                for v in vals:
                    if v not in seen:
                        seen.add(v)
                        unique_vals.append(v)
                sheets.append((f"{col}列表"[:31], [col], [[v] for v in unique_vals]))

            if not sheets:
                return answer

            filename = export_sheets_to_excel(sheets)
            if not filename:
                return answer

            url = f"/api/v1/chat/exports/{filename}"
            # 汇总提示文本
            tips = []
            if has_detail_overflow:
                tips.append(f"明细数据 {len(detail_hits)} 条")
            for col, vals in overflow_distinct.items():
                tips.append(f"{col} {len(vals)} 个")
            return (
                f"{answer}\n\n"
                f"📄 {'、'.join(tips)}已超出页面展示范围，"
                f"[点击下载完整明细（Excel）]({url})（文件保留 24 小时）"
            )
        except Exception as e:
            logger.warning(f"附加明细下载链接失败: {e}")
            return answer

    def _append_rows_download_link(self, answer: str, columns: list, rows: list) -> str:
        """基于行列数据导出并附加下载链接（旧接口路径用）"""
        try:
            if not rows or len(rows) <= self.DETAIL_INLINE_LIMIT:
                return answer

            from .detail_export import export_rows_to_excel
            filename = export_rows_to_excel(list(columns or []), [list(r) for r in rows])
            if not filename:
                return answer

            url = f"/api/v1/chat/exports/{filename}"
            return (
                f"{answer}\n\n"
                f"📄 明细共 {len(rows)} 条，已超出页面展示范围，"
                f"[点击下载完整明细（Excel）]({url})（文件保留 24 小时）"
            )
        except Exception as e:
            logger.warning(f"附加明细下载链接失败: {e}")
            return answer

    def _answer_projection(self, plan: QueryPlan, hits: list) -> Optional[str]:
        """属性定向回答：从命中记录中抽取"用户所问的那一列"，直接给出答案。

        用于"胡兰桂在哪个部门"这类问题（实体 + 指定属性），避免把整行明细
        拼成表格返回。数据来自 hit.payload（Record 模式已把整行结构化数据放进 payload）。

        Returns:
            定向答案文本；返回 None 表示无法定向（回退到常规合成流程）。
        """
        column = (plan.projection_column or "").strip()
        if not column or not hits:
            return None

        values: list[str] = []
        for h in hits:
            payload = h.payload or {}
            # 聚合 / 去重列表类 hit 的 payload 不是整行数据，跳过
            if payload.get("type") in ("aggregate", "distinct_list"):
                continue
            raw = payload.get(column)
            if raw is None:
                continue
            val = str(raw).strip()
            if not val:
                continue
            if val not in values:
                values.append(val)

        if not values:
            return None

        filter_desc = self._describe_filters(plan.filters) if plan.filters else ""
        subject = f"{filter_desc}的" if filter_desc else ""

        if len(values) == 1:
            return f"{subject}{column}：**{values[0]}**"

        listed = "\n".join(f"- {v}" for v in values)
        return f"{subject}{column}（共 {len(values)} 个）：\n\n{listed}"

    async def synthesize_multi(
        self,
        query: str,
        plan: QueryPlan,
        multi_result: MultiRetrievalResult,
        context: Optional[AgentContext] = None,
    ) -> tuple[str, list[dict]]:
        """
        基于多路检索结果合成最终回答（新架构主接口）

        Args:
            query: 原始用户问题
            plan: 查询计划
            multi_result: 多路检索结果（含各路独立结果和融合结果）
            context: Agent 上下文

        Returns:
            (answer, references) 元组
        """
        # 判断有哪些路有结果
        has_sql = bool(multi_result.sql_hits and multi_result.sql_success)
        has_vector = bool(multi_result.vector_hits and multi_result.vector_success)
        has_keyword = bool(multi_result.keyword_hits and multi_result.keyword_success)

        if not has_sql and not has_vector and not has_keyword:
            # 三路都没结果
            answer = self._synthesize_failure_multi(query, plan, multi_result)
            references = self._build_references_multi(multi_result, context)
            return answer, references

        # 分组聚合排名（GROUP_AGG）：走确定性格式化，直接给出排名结果，
        # 避免 LLM 误判为"没有汇总数据"而拒答。
        if has_sql and any(
            h.payload and h.payload.get("type") == "group_agg"
            for h in multi_result.sql_hits
        ):
            answer = self._format_group_agg(multi_result.sql_hits, plan)
            references = self._build_references_multi(multi_result, context)
            # 排名结果已在正文完整给出（含 TopN / 分组总数），无需再附加明细下载链接
            return answer, references

        # 属性定向问答：用户问的是"某实体的某属性"（如"胡兰桂在哪个部门"），
        # 直接回答该属性值，避免把整行明细 dump 成表格
        if has_sql and plan.projection_column:
            projection_answer = self._answer_projection(plan, multi_result.sql_hits)
            if projection_answer:
                references = self._build_references_multi(multi_result, context)
                return (
                    self._append_detail_download_link(
                        projection_answer, multi_result.sql_hits
                    ),
                    references,
                )

        # 如果 SQL 有聚合结果（精确统计），优先用 SQL
        if has_sql and self._is_aggregate_sql(multi_result.sql_hits):
            if has_vector or has_keyword:
                # 混合：SQL 精确 + 向量/关键词补充
                answer = await self._synthesize_multi_hybrid(query, plan, multi_result)
            else:
                # 只有 SQL 聚合
                answer = await self._synthesize_multi_sql(query, plan, multi_result)
        elif has_sql and has_vector:
            # 混合模式
            answer = await self._synthesize_multi_hybrid(query, plan, multi_result)
        elif has_sql:
            # 只有 SQL
            answer = await self._synthesize_multi_sql(query, plan, multi_result)
        elif has_vector:
            # 只有向量（语义）
            answer = await self._synthesize_multi_semantic(query, plan, multi_result)
        else:
            # 只有关键词
            answer = self._synthesize_multi_keyword(query, plan, multi_result)

        # 明细超过内联上限时，附上完整明细下载链接
        answer = self._append_detail_download_link(answer, multi_result.sql_hits)

        # 生成引用
        references = self._build_references_multi(multi_result, context)
        return answer, references

    def _is_aggregate_sql(self, sql_hits: list[RetrievalHit]) -> bool:
        """判断 SQL 结果是否为聚合查询（COUNT/SUM/AVG 等）"""
        if not sql_hits:
            return False
        first = sql_hits[0]
        return first.payload.get("type") == "aggregate"

    def _format_group_agg(self, sql_hits: list[RetrievalHit], plan: QueryPlan) -> str:
        """格式化分组聚合排名结果（确定性输出，不依赖 LLM）

        支持两种形态：
        - 单值："哪个业务员销售的最多" → 直接给出最优分组及其聚合值
        - 排名："销售额排名前三的业务员" → 有序排名列表
        """
        hits = [
            h for h in sql_hits if h.payload and h.payload.get("type") == "group_agg"
        ]
        if not hits:
            return "未找到相关结果。"

        valid = [h for h in hits if not h.payload.get("empty")]
        filter_desc = self._describe_filters(plan.filters)
        scope = f"在{filter_desc}范围内，" if filter_desc else ""

        # 空结果：明确告知"该范围内无数据"
        if not valid:
            p0 = hits[0].payload
            dim = p0.get("group_column") or plan.group_by or "分组"
            metric = p0.get("metric_column") or plan.metric_column
            metric_desc = metric or "记录数"
            return (
                f"根据现有数据，{scope}没有可用于按「{dim}」统计{metric_desc}的记录。"
            )

        first = valid[0].payload
        dim = first.get("group_column") or plan.group_by or "分组"
        metric = first.get("metric_column") or plan.metric_column or ""
        agg_func = first.get("agg_func") or plan.agg_func or "sum"
        order_dir = (first.get("order_dir") or plan.order_dir or "DESC").upper()
        total_groups = first.get("_total_groups")

        op_label = {"sum": "合计", "avg": "平均", "count": "数量", "max": "最大", "min": "最小"}.get(
            agg_func, "合计"
        )
        metric_desc = f"{metric}{op_label}" if metric else "记录数"
        direction = "最少" if order_dir == "ASC" else "最多"

        # 单值形态：只取到一个分组（limit=1）且无更多分组
        if len(valid) == 1 and (plan.limit == 1 or total_groups in (None, 1)):
            hp = valid[0].payload
            return (
                f"{scope}{metric_desc}{direction}的是「{hp.get('group_value')}」，"
                f"{metric_desc}为 **{hp.get('value')}**。"
            )

        # 排名形态
        lines = []
        for h in valid:
            hp = h.payload
            lines.append(
                f"{hp.get('rank', '-')}. 「{hp.get('group_value')}」 — {metric_desc} {hp.get('value')}"
            )
        shown = len(valid)
        if total_groups and total_groups > shown:
            header = (
                f"{scope}按「{dim}」统计的{metric_desc}排名"
                f"（前 {shown} 名 / 共 {total_groups} 个分组）：\n\n"
            )
        else:
            header = f"{scope}按「{dim}」统计的{metric_desc}排名（共 {shown} 个）：\n\n"
        return header + "\n".join(lines)

    async def _synthesize_multi_sql(
        self, query: str, plan: QueryPlan, multi_result: MultiRetrievalResult
    ) -> str:
        """基于 SQL 结果生成回答（多路结果版）

        单表：走规则生成（精确、快速、不依赖LLM）
        多表：走 LLM 综合（需要跨表整合信息）
        """
        is_multi_table = multi_result.metadata.get("multi_table", False)

        # 多表场景：走 LLM 综合，因为规则生成是基于单表 plan 的，无法跨表整合
        if is_multi_table:
            llm = self._get_llm()
            if llm is not None:
                try:
                    context_text = self._build_multi_context(multi_result, plan)
                    answer = await self._llm_text(
                        prompt=f"用户问题：{query}",
                        system_prompt=SYNTHESIS_SYSTEM_PROMPT + "\n\n" + context_text,
                        temperature=0.3,
                        max_tokens=2048,
                    )
                    return answer.strip()
                except Exception as e:
                    logger.warning(f"LLM multi-sql synthesis failed, using rule-based: {e}")
                    # 降级到规则生成（展示第一个表的结果）

        # 单表场景（或 LLM 不可用/失败降级）：走规则生成
        sql_result = self._hits_to_sql_result(multi_result.sql_hits, plan)
        return await self._synthesize_sql(query, plan, sql_result)

    async def _synthesize_multi_semantic(
        self, query: str, plan: QueryPlan, multi_result: MultiRetrievalResult
    ) -> str:
        """基于向量检索结果生成回答（多路结果版）"""
        retrieval_result = self._hits_to_retrieval_result(multi_result.merged_hits)
        return await self._synthesize_semantic(query, plan, retrieval_result)

    def _synthesize_multi_keyword(
        self, query: str, plan: QueryPlan, multi_result: MultiRetrievalResult
    ) -> str:
        """基于关键词检索结果生成回答"""
        hits = multi_result.keyword_hits
        if not hits:
            return "未找到相关结果。"

        parts = ["找到以下相关内容（关键词匹配）：\n"]
        for i, hit in enumerate(hits[:5], 1):
            sheet = hit.sheet_name or "未知表"
            text = hit.content[:200] if hit.content else "(无内容)"
            matched = hit.metadata.get("matched_keywords", [])
            kw_info = f" [匹配: {', '.join(matched)}]" if matched else ""
            parts.append(f"**{i}. {sheet}**{kw_info}\n{text}...\n")

        return "\n".join(parts)

    async def _synthesize_multi_hybrid(
        self, query: str, plan: QueryPlan, multi_result: MultiRetrievalResult
    ) -> str:
        """混合模式：多路结果融合生成回答"""

        # 判断是否为多表查询
        is_multi_table = multi_result.metadata.get("multi_table", False)

        # 结构化查询（SQL 有结果）：
        # - 单表：优先用 SQL 规则生成，不走 LLM（避免 LLM 搞错统计数字）
        # - 多表：必须走 LLM 综合，因为规则生成是基于单表 plan 的，无法跨表整合
        structured_ops = (
            QueryOperation.COUNT, QueryOperation.SUM, QueryOperation.AVG,
            QueryOperation.LIST, QueryOperation.DETAIL,
            QueryOperation.TOP_N, QueryOperation.GROUP_COUNT,
            QueryOperation.GROUP_AGG,
        )
        if (plan.operation in structured_ops
                and multi_result.sql_hits and multi_result.sql_success
                and not is_multi_table):
            return await self._synthesize_multi_sql(query, plan, multi_result)

        llm = self._get_llm()
        if llm is None:
            # 没有 LLM，优先展示 SQL 结果
            if multi_result.sql_hits:
                return await self._synthesize_multi_sql(query, plan, multi_result)
            return await self._synthesize_multi_semantic(query, plan, multi_result)

        try:
            context_text = self._build_multi_context(multi_result, plan)

            answer = await self._llm_text(
                prompt=f"用户问题：{query}",
                system_prompt=SYNTHESIS_SYSTEM_PROMPT + "\n\n" + context_text,
                temperature=0.5,
                max_tokens=2048,
            )
            return answer.strip()

        except Exception as e:
            logger.warning(f"LLM multi synthesis failed: {e}")
            # 降级：优先用 SQL 结果
            if multi_result.sql_hits:
                return await self._synthesize_multi_sql(query, plan, multi_result)
            return await self._synthesize_multi_semantic(query, plan, multi_result)

    def _build_multi_context(
        self, multi_result: MultiRetrievalResult, plan: QueryPlan
    ) -> str:
        """构建多路结果的 LLM 上下文文本"""
        parts = []

        # 判断是否为多表查询
        is_multi_table = multi_result.metadata.get("multi_table", False)

        # SQL 结果（精确数据，优先展示）
        if multi_result.sql_hits:
            parts.append("## 精确数据（来自数据库）")

            # 分组聚合排名（已按维度分组汇总）——优先且排他展示
            group_agg_hits = [
                h for h in multi_result.sql_hits
                if h.payload and h.payload.get("type") == "group_agg"
            ]
            if group_agg_hits:
                valid = [h for h in group_agg_hits if not h.payload.get("empty")]
                if valid:
                    p0 = valid[0].payload
                    dim = p0.get("group_column", "")
                    metric = p0.get("metric_column", "")
                    agg = p0.get("agg_func", "sum")
                    op_label = {
                        "sum": "合计", "avg": "平均", "count": "数量",
                        "max": "最大", "min": "最小",
                    }.get(agg, "合计")
                    metric_desc = f"{metric}{op_label}" if metric else "记录数"
                    parts.append(
                        f"（以下为**已按「{dim}」分组汇总**的{metric_desc}排名，已按聚合值排序，"
                        f"请直接据此给出排名结论，不要说没有汇总数据）"
                    )
                    for h in valid:
                        hp = h.payload
                        parts.append(f"{hp.get('rank', '-')}. {hp.get('group_value')} : {hp.get('value')}")
                else:
                    parts.append("该范围内没有匹配的分组数据。")
                parts.append("")
            elif is_multi_table:
                # --- 多表场景：按来源表分组展示 ---
                table_groups: dict[str, list] = {}
                for h in multi_result.sql_hits:
                    tname = h.metadata.get("table_name", "未知表")
                    if tname not in table_groups:
                        table_groups[tname] = []
                    table_groups[tname].append(h)

                for tname, hits in table_groups.items():
                    parts.append(f"### 【{tname}】")

                    # 分离聚合和明细
                    agg_hit = None
                    detail_hits = []
                    distinct_hits = []
                    for h in hits:
                        if h.payload and h.payload.get("type") == "aggregate":
                            agg_hit = h
                        elif h.payload and h.payload.get("type") == "distinct_list":
                            distinct_hits.append(h)
                        else:
                            detail_hits.append(h)

                    if agg_hit:
                        val = agg_hit.payload.get("value", 0)
                        target = (agg_hit.payload.get("target_column")
                                  or plan.target_column_alias
                                  or plan.target_column
                                  or "结果")
                        total_count = agg_hit.payload.get("total_count", len(detail_hits))
                        parts.append(f"统计结果：{target} = **{val}**（共 {total_count} 条记录）")
                        if detail_hits:
                            parts.append("")
                            parts.append("明细列表：")
                            # 聚合查询的明细：展示前 30 条（足够 LLM 了解数据分布）
                            show_count = min(30, len(detail_hits))
                            for i, h in enumerate(detail_hits[:show_count], 1):
                                content = h.content[:100] if h.content else "(无)"
                                parts.append(f"{i}. {content}")
                            if len(detail_hits) > show_count:
                                parts.append(f"... 还有 {len(detail_hits) - show_count} 条")
                    elif distinct_hits:
                        # 去重列表：不超过上限时全量展示，超限展示前 N 条
                        valid_distinct = [h for h in distinct_hits if not (h.payload and h.payload.get("empty"))]
                        total_distinct = len(valid_distinct)
                        parts.append(f"去重列表（共 {total_distinct} 个）：")
                        show_n = self.DETAIL_INLINE_LIMIT
                        for i, h in enumerate(valid_distinct[:show_n], 1):
                            content = h.content.strip() if h.content else "(无)"
                            parts.append(f"{i}. {content}")
                        if total_distinct > show_n:
                            parts.append(f"（其余 {total_distinct - show_n} 个见回答末尾的下载文件）")
                    else:
                        # 列表/详情
                        total_detail = len(detail_hits)
                        # 从第一个 hit 获取真实总数
                        if detail_hits and detail_hits[0].payload:
                            real_total = detail_hits[0].payload.get("_total_count")
                            if real_total:
                                total_detail = real_total
                        parts.append(f"列表结果（共 {total_detail} 条）：")
                        show_count = min(30, len(detail_hits))
                        for i, h in enumerate(detail_hits[:show_count], 1):
                            content = h.content[:120] if h.content else "(无)"
                            parts.append(f"{i}. {content}")
                        if len(detail_hits) > show_count:
                            parts.append(f"... 还有 {len(detail_hits) - show_count} 条")
                    parts.append("")
            else:
                # --- 单表场景 ---
                # 分离聚合、去重列表和明细
                agg_hit = None
                detail_hits = []
                distinct_hits = []
                for h in multi_result.sql_hits:
                    if h.payload and h.payload.get("type") == "aggregate":
                        agg_hit = h
                    elif h.payload and h.payload.get("type") == "distinct_list":
                        distinct_hits.append(h)
                    else:
                        detail_hits.append(h)

                if agg_hit:
                    val = agg_hit.payload.get("value", 0)
                    target = plan.target_column_alias or plan.target_column or "结果"
                    filters_desc = ""
                    if plan.filters:
                        filters_desc = self._describe_filters(plan.filters)
                    total_count = agg_hit.payload.get("total_count", len(detail_hits))
                    parts.append(f"{filters_desc}的{target}：**{val}**（共 {total_count} 条记录）")
                    # 如果有明细，也展示出来
                    if detail_hits:
                        parts.append("")
                        parts.append("明细列表：")
                        show_count = min(30, len(detail_hits))
                        for i, h in enumerate(detail_hits[:show_count], 1):
                            content = h.content[:80] if h.content else "(无)"
                            parts.append(f"{i}. {content}")
                        if len(detail_hits) > show_count:
                            parts.append(f"... 还有 {len(detail_hits) - show_count} 条")
                elif distinct_hits:
                    # 去重列表：不超过上限时全量展示，超限展示前 N 条
                    valid_distinct = [h for h in distinct_hits if not (h.payload and h.payload.get("empty"))]
                    total_distinct = len(valid_distinct)
                    col_name = plan.target_column_alias or plan.target_column or "结果"
                    parts.append(f"{col_name}列表（共 {total_distinct} 个）：")
                    show_n = self.DETAIL_INLINE_LIMIT
                    for i, h in enumerate(valid_distinct[:show_n], 1):
                        content = h.content.strip() if h.content else "(无)"
                        parts.append(f"{i}. {content}")
                    if total_distinct > show_n:
                        parts.append(f"（其余 {total_distinct - show_n} 个见回答末尾的下载文件）")
                else:
                    # 列表结果
                    total_detail = len(detail_hits)
                    if detail_hits and detail_hits[0].payload:
                        real_total = detail_hits[0].payload.get("_total_count")
                        if real_total:
                            total_detail = real_total
                    parts.append(f"列表结果（共 {total_detail} 条）：")
                    parts.append("| 序号 | 内容 |")
                    parts.append("| --- | --- |")
                    show_count = min(30, len(detail_hits))
                    for i, hit in enumerate(detail_hits[:show_count], 1):
                        content = hit.content[:100] if hit.content else "(无)"
                        parts.append(f"| {i} | {content} |")
                    if len(detail_hits) > show_count:
                        parts.append(f"| ... | 还有 {len(detail_hits) - show_count} 条 |")
            parts.append("")

        # 融合结果（语义相关性排序）
        if multi_result.merged_hits:
            parts.append("## 相关参考（按相关性排序）")
            for i, hit in enumerate(multi_result.merged_hits[:8], 1):
                source = hit.payload.get("sources", [])
                source_str = f" [来源: {','.join(source)}]" if source else ""
                sheet = hit.sheet_name or "未知"
                text = hit.content[:150] if hit.content else "(无)"
                parts.append(f"[{i}] 《{sheet}》{source_str}\n{text}")
            parts.append("")

        return "\n".join(parts)

    def _synthesize_failure_multi(
        self, query: str, plan: QueryPlan, multi_result: MultiRetrievalResult
    ) -> str:
        """多路都失败时的回答"""
        reasons = []
        if not multi_result.sql_success and multi_result.sql_error:
            reasons.append(f"数据查询失败：{multi_result.sql_error}")
        if not multi_result.vector_success and multi_result.vector_error:
            reasons.append(f"语义检索失败：{multi_result.vector_error}")
        if not multi_result.keyword_success and multi_result.keyword_error:
            reasons.append(f"关键词检索失败：{multi_result.keyword_error}")

        if reasons:
            return "抱歉，未能获取到相关数据。\n\n原因：\n- " + "\n- ".join(reasons)
        return "根据现有资料，无法找到相关结果。请尝试换一种问法，或者确认数据是否存在。"

    def _build_references_multi(
        self, multi_result: MultiRetrievalResult, context: Optional[AgentContext]
    ) -> list[dict]:
        """基于多路结果生成引用列表

        只展示 SQL 精确查询结果作为引用，向量/关键词检索结果不展示给用户
        （向量检索仍参与内部答案合成，但不作为引用呈现）
        """
        references = []

        # SQL 结果引用（只保留精确的 SQL 查询结果）
        if multi_result.sql_hits:
            doc_name = ""
            doc_id = ""
            if hasattr(context, "debug_info") and context.debug_info:
                doc_name = context.debug_info.get("file_name", "")
                doc_id = context.debug_info.get("document_id", "")

            # 分离聚合 / 分组聚合 / 明细 hit
            agg_hit = None
            group_agg_hits = []
            detail_hits = []
            for h in multi_result.sql_hits:
                htype = (h.payload or {}).get("type")
                if htype == "aggregate":
                    agg_hit = h
                elif htype == "group_agg":
                    group_agg_hits.append(h)
                else:
                    detail_hits.append(h)

            if group_agg_hits:
                # 分组聚合：汇总为一条排名说明引用
                valid = [h for h in group_agg_hits if not h.payload.get("empty")]
                if valid:
                    p0 = valid[0].payload
                    dim = p0.get("group_column", "")
                    metric = p0.get("metric_column", "")
                    text = "、".join(
                        f"{h.payload.get('group_value')}={h.payload.get('value')}"
                        for h in valid[:10]
                    )
                    references.append({
                        "chunk_id": "sql_group_agg",
                        "document_id": doc_id,
                        "document_name": doc_name,
                        "chunk_index": 0,
                        "text": f"按「{dim}」分组的{metric or '记录数'}排名：{text}",
                        "score": 1.0,
                        "source_type": "sql",
                    })
            elif agg_hit:
                val = agg_hit.payload.get("value", 0)
                references.append({
                    "chunk_id": "sql_aggregate",
                    "document_id": doc_id,
                    "document_name": doc_name,
                    "chunk_index": 0,
                    "text": f"SQL聚合查询结果：{val}",
                    "score": 1.0,
                    "source_type": "sql",
                })
                # 聚合查询有明细时，也把前5条明细加入引用
                for i, hit in enumerate(detail_hits[:5]):
                    references.append({
                        "chunk_id": hit.record_id or f"sql_detail_{i}",
                        "document_id": hit.document_id or doc_id,
                        "document_name": doc_name or hit.sheet_name,
                        "chunk_index": hit.row_number,
                        "text": hit.content,
                        "score": hit.score,
                        "page_num": None,
                        "source_type": "sql",
                        "sheet_name": hit.sheet_name,
                    })
            else:
                for i, hit in enumerate(multi_result.sql_hits[:5]):
                    references.append({
                        "chunk_id": hit.record_id or f"sql_{i}",
                        "document_id": hit.document_id or doc_id,
                        "document_name": doc_name or hit.sheet_name,
                        "chunk_index": hit.row_number,
                        "text": hit.content,
                        "score": hit.score,
                        "page_num": None,
                        "source_type": "sql",
                        "sheet_name": hit.sheet_name,
                    })

        # 注：向量检索和关键词检索结果仍参与内部答案合成，
        # 但不再作为引用展示给用户，避免模糊匹配结果造成困惑

        return references

    def _hits_to_sql_result(
        self, hits: list[RetrievalHit], plan: QueryPlan
    ) -> SQLQueryResult:
        """RetrievalHit 转 SQLQueryResult（兼容旧接口）"""
        if not hits:
            return SQLQueryResult(success=False, error="no results")

        # 分离聚合 hit 和明细 hit
        aggregate_hit = None
        detail_hits: list[RetrievalHit] = []

        for hit in hits:
            if hit.payload and hit.payload.get("type") == "aggregate":
                aggregate_hit = hit
            else:
                detail_hits.append(hit)

        # 如果有聚合结果
        if aggregate_hit:
            val = aggregate_hit.payload.get("value", 0)
            result = SQLQueryResult(
                success=True,
                columns=[plan.target_column_alias or plan.target_column or "结果"],
                rows=[[val]],
                row_count=1,
            )
            # 如果有明细数据，附加到 detail_items
            if detail_hits:
                # 从明细 hit 中提取目标列的值（用于展示）
                detail_items = []
                target_col = plan.target_column or ""
                target_col_alias = plan.target_column_alias or ""
                for hit in detail_hits:
                    payload = hit.payload or {}
                    # 优先用目标列的值，其次用 content
                    if target_col and target_col in payload:
                        detail_items.append(str(payload[target_col]))
                    elif target_col_alias and target_col_alias in payload:
                        detail_items.append(str(payload[target_col_alias]))
                    elif hit.content:
                        detail_items.append(hit.content[:100])
                    else:
                        detail_items.append(f"第{hit.row_number}行")
                result.detail_items = detail_items  # type: ignore
                # 同时保存明细 hit 的总数（用于判断是否还有更多）
                total_count = aggregate_hit.payload.get("total_count", len(detail_items))
                result.total_count = total_count  # type: ignore
            return result

        # 纯列表结果
        # 判断是否是去重列表（distinct_list）
        is_distinct = False
        distinct_column = ""
        if hits and hits[0].payload and hits[0].payload.get("type") == "distinct_list":
            is_distinct = True
            distinct_column = hits[0].payload.get("column_alias") or hits[0].payload.get("column") or ""

        if is_distinct:
            # 去重列表：列名是目标列，值是去重后的值
            col_name = distinct_column or plan.target_column_alias or plan.target_column or "结果"
            # 过滤掉空 hit
            valid_hits = [h for h in hits if not (h.payload and h.payload.get("empty"))]
            columns = [col_name]
            rows = [[h.content] for h in valid_hits]
        else:
            columns = ["内容"]
            rows = [[h.content] for h in hits]

        # 从第一个 hit 中提取总数信息（SQL 精确查询不受 top_k 限制）
        total_count = None
        if hits and hits[0].payload:
            total_count = hits[0].payload.get("_total_count")

        result = SQLQueryResult(
            success=True, columns=columns, rows=rows, row_count=len(rows)
        )
        # 附加真实总数（可能大于 row_count，因为 row_count 是展示的条数）
        if total_count is not None:
            result.total_count = total_count  # type: ignore
        # 标记是否去重列表
        if is_distinct:
            result.is_distinct_list = True  # type: ignore
        return result

    def _hits_to_retrieval_result(self, hits: list[RetrievalHit]) -> RetrievalQueryResult:
        """RetrievalHit 转 RetrievalQueryResult（兼容旧接口）"""
        results = []
        max_score = 0.0
        for hit in hits:
            results.append({
                "chunk_id": hit.record_id,
                "document_id": hit.document_id,
                "text": hit.content,
                "score": hit.score,
                "page_num": hit.metadata.get("page_num", 0),
            })
            if hit.score > max_score:
                max_score = hit.score

        return RetrievalQueryResult(
            success=bool(hits),
            results=results,
            total=len(hits),
            max_score=max_score,
        )

    # ------------------------------------------------------------------
    # 主入口（旧接口，向后兼容）
    # ------------------------------------------------------------------

    async def synthesize_from_hits(
        self,
        query: str,
        plan: QueryPlan,
        sql_hits: Optional[list] = None,
        vector_hits: Optional[list] = None,
        context: Optional[AgentContext] = None,
    ) -> tuple[str, list[dict]]:
        """从原始 hit 列表合成回答（供多意图路径的降级场景使用）

        内部完成 RetrievalHit → SQLQueryResult / RetrievalQueryResult 的转换，
        调用方无需依赖私有转换方法，也避免参数签名不匹配。

        Args:
            query: 原始用户问题
            plan: 查询计划
            sql_hits: SQL 命中列表（RetrievalHit）
            vector_hits: 向量命中列表（RetrievalHit）
            context: Agent 上下文

        Returns:
            (answer, references) 元组
        """
        sql_result = self._hits_to_sql_result(sql_hits, plan) if sql_hits else None
        retrieval_result = self._hits_to_retrieval_result(vector_hits) if vector_hits else None
        return await self.synthesize(query, plan, sql_result, retrieval_result, context)

    async def synthesize(
        self,
        query: str,
        plan: QueryPlan,
        sql_result: Optional[SQLQueryResult] = None,
        retrieval_result: Optional[RetrievalQueryResult] = None,
        context: Optional[AgentContext] = None,
    ) -> tuple[str, list[dict]]:
        """
        融合结果，生成最终回答

        Args:
            query: 原始用户问题
            plan: 查询计划
            sql_result: SQL 查询结果（可选）
            retrieval_result: 向量检索结果（可选）
            context: Agent 上下文

        Returns:
            (answer, references) 元组
        """
        # 决定使用哪种合成策略
        if sql_result and sql_result.success and sql_result.rows:
            # 有 SQL 结果 → 优先用 SQL 结果
            if retrieval_result and retrieval_result.success and retrieval_result.results:
                # 双路都有 → 混合模式，SQL 为主，检索为辅
                answer = await self._synthesize_hybrid(
                    query, plan, sql_result, retrieval_result
                )
            else:
                # 只有 SQL → 纯 SQL 回答
                answer = await self._synthesize_sql(query, plan, sql_result)
        elif retrieval_result and retrieval_result.success and retrieval_result.results:
            # 只有向量检索 → 纯语义回答
            answer = await self._synthesize_semantic(query, plan, retrieval_result)
        else:
            # 都没有 → 失败回答
            answer = self._synthesize_failure(query, plan, sql_result, retrieval_result)

        # 明细超过内联上限时，附上完整明细下载链接（旧接口路径：基于 sql_result 行列数据导出）
        if sql_result and sql_result.success and sql_result.rows:
            answer = self._append_rows_download_link(
                answer, sql_result.columns, sql_result.rows
            )

        # 生成引用
        references = self._build_references(sql_result, retrieval_result, context)

        return answer, references

    # ------------------------------------------------------------------
    # SQL 结果合成
    # ------------------------------------------------------------------

    async def _synthesize_sql(
        self, query: str, plan: QueryPlan, sql_result: SQLQueryResult
    ) -> str:
        """基于 SQL 结果生成回答"""
        # 简单场景用规则生成，速度更快，也更准确
        simple_answer = self._rule_based_sql_answer(query, plan, sql_result)

        # 结构化操作类型直接用规则生成，不走 LLM
        # 原因：
        # 1. 聚合类（COUNT/SUM/AVG）：LLM 可能被明细数字误导
        # 2. 列表类（LIST/DETAIL/TOP_N/GROUP_COUNT）：LLM 可能搞不清展示数和总数
        # 3. 规则生成是精确的，SQL 数据也是精确的，不需要 LLM 润色
        structured_ops = (
            QueryOperation.COUNT, QueryOperation.SUM, QueryOperation.AVG,
            QueryOperation.LIST, QueryOperation.DETAIL,
            QueryOperation.TOP_N, QueryOperation.GROUP_COUNT,
            QueryOperation.EXISTS,
        )
        if plan.operation in structured_ops:
            return simple_answer

        if not self.use_llm_for_synthesis:
            return simple_answer

        # UNKNOWN 等不确定类型，用 LLM 润色
        llm = self._get_llm()
        if llm is None:
            return simple_answer

        try:
            result_table = sql_result.to_markdown_table(max_rows=20)
            # 使用真实总数（total_count 如果有的话），否则用 row_count
            total = getattr(sql_result, "total_count", None)
            total_display = total if total is not None else sql_result.row_count
            context = f"""查询结果（来自数据表）：

{result_table}

共 {total_display} 条结果（展示前 {sql_result.row_count} 条）。"""

            answer = await self._llm_text(
                prompt=f"用户问题：{query}",
                system_prompt=SYNTHESIS_SYSTEM_PROMPT + "\n\n" + context,
                temperature=0.3,
                max_tokens=1200,
            )

            return answer.strip()

        except Exception as e:
            logger.warning(f"LLM synthesis failed, using rule-based: {e}")
            return simple_answer

    def _rule_based_sql_answer(
        self, query: str, plan: QueryPlan, sql_result: SQLQueryResult
    ) -> str:
        """基于规则的 SQL 结果回答（快速、不依赖 LLM）"""
        operation = plan.operation.value
        target = plan.target_column_alias or plan.target_column or "结果"

        # SUM / AVG 类（求和 / 平均）：直接给出一句话结论，不展示表格
        if operation in ("sum", "avg") and sql_result.rows and len(sql_result.rows) > 0:
            val = sql_result.get_single_value()
            if val is not None:
                filter_desc = self._describe_filters(plan.filters) if plan.filters else ""
                op_label = "合计" if operation == "sum" else "平均值"
                total = getattr(sql_result, "total_count", None)
                cnt_desc = f"，共 {total} 条记录" if total else ""
                if filter_desc:
                    return f"根据数据，{filter_desc}的{target}{op_label}为 **{val}**{cnt_desc}。"
                return f"根据数据，{target}{op_label}为 **{val}**{cnt_desc}。"

        # COUNT 类
        if operation == "count" and sql_result.rows and len(sql_result.rows) > 0:
            val = sql_result.get_single_value()
            if val is not None:
                # 检查是否附带明细数据（从 sql_result 的附加信息中获取）
                detail_items = getattr(sql_result, "detail_items", None)
                filter_desc = self._describe_filters(plan.filters) if plan.filters else ""
                target = plan.target_column_alias or plan.target_column or "结果"

                if detail_items:
                    # 有明细：总数 + 明细列表（超过上限时展示前 N 条，完整明细走下载链接）
                    scope = f"{filter_desc}的" if filter_desc else ""
                    prefix = f"根据数据统计，{scope}{target}共有 **{val}** 个。\n\n明细列表：\n\n"
                    if len(detail_items) > self.DETAIL_INLINE_LIMIT:
                        list_text = "\n".join(
                            f"- {item}" for item in detail_items[: self.DETAIL_INLINE_LIMIT]
                        )
                        list_text += (
                            f"\n\n（明细较多，仅展示前 {self.DETAIL_INLINE_LIMIT} 条，"
                            f"完整明细请通过下方链接下载）"
                        )
                    else:
                        list_text = "\n".join(f"- {item}" for item in detail_items)
                    return prefix + list_text
                else:
                    # 只有总数
                    if plan.filters:
                        return f"根据数据统计，{filter_desc}的{target}共有 **{val}** 个。"
                    return f"根据数据统计，{target}共有 **{val}** 个。"

        # LIST 类
        if operation == "list":
            items = [str(row[0]) for row in sql_result.rows if row and row[0] is not None]
            if items:
                # 真实总数（SQL 精确查询，不受 top_k 限制）
                total = getattr(sql_result, "total_count", None)
                shown_count = len(items)
                if total is None:
                    total = shown_count

                is_distinct = getattr(sql_result, "is_distinct_list", False)
                col_name = sql_result.columns[0] if sql_result.columns else target
                unit = "个" if is_distinct else "条"

                # LIST/DETAIL 类查询：不超过上限时全量展示；
                # 超过上限时展示前 N 条，完整明细通过下载链接获取
                if plan.filters:
                    filter_desc = self._describe_filters(plan.filters)
                    prefix = f"根据数据，{filter_desc}的{col_name}共有 **{total}** {unit}：\n\n"
                else:
                    prefix = f"根据数据，{col_name}共有 **{total}** {unit}：\n\n"

                if len(items) > self.DETAIL_INLINE_LIMIT:
                    list_text = "\n".join(f"- {item}" for item in items[: self.DETAIL_INLINE_LIMIT])
                    list_text += (
                        f"\n\n（明细较多，仅展示前 {self.DETAIL_INLINE_LIMIT} {unit}，"
                        f"完整明细请通过下方链接下载）"
                    )
                else:
                    list_text = "\n".join(f"- {item}" for item in items)
                return prefix + list_text

        # GROUP_COUNT 类
        if operation == "group_count" and sql_result.columns:
            return f"根据统计数据：\n\n{sql_result.to_markdown_table()}"

        # TOP_N 类
        if operation == "top_n":
            return f"根据排名数据：\n\n{sql_result.to_markdown_table()}"

        # 默认：表格展示（超过上限时截断展示，完整明细走下载链接）
        if sql_result.row_count > self.DETAIL_INLINE_LIMIT:
            return (
                f"查询结果共 {sql_result.row_count} 条，以下为前 {self.DETAIL_INLINE_LIMIT} 条"
                f"（完整明细请通过下方链接下载）：\n\n"
                f"{sql_result.to_markdown_table(max_rows=self.DETAIL_INLINE_LIMIT)}"
            )
        return f"查询结果如下（共 {sql_result.row_count} 条）：\n\n{sql_result.to_markdown_table()}"

    def _describe_filters(self, filters: list[FilterInfo]) -> str:
        """将过滤条件描述为自然语言"""
        if not filters:
            return ""

        parts = []
        for f in filters:
            col = f.column_alias or f.column
            val = f.value
            if f.operator == "=":
                parts.append(f"{col}是「{val}」")
            elif f.operator == "!=":
                parts.append(f"{col}不是「{val}」")
            else:
                parts.append(f"{col} {f.operator} {val}")

        if len(parts) == 1:
            return parts[0]
        elif len(parts) == 2:
            return f"{parts[0]}且{parts[1]}"
        else:
            return "、".join(parts[:-1]) + f"且{parts[-1]}"

    # ------------------------------------------------------------------
    # 语义检索结果合成
    # ------------------------------------------------------------------

    async def _synthesize_semantic(
        self, query: str, plan: QueryPlan, retrieval_result: RetrievalQueryResult
    ) -> str:
        """基于向量检索结果生成回答"""
        llm = self._get_llm()
        if llm is None:
            # 没有 LLM，直接返回检索结果文本
            return self._build_retrieval_text(retrieval_result)

        try:
            context = self._build_retrieval_context(retrieval_result)

            answer = await self._llm_text(
                prompt=f"用户问题：{query}",
                system_prompt=SYNTHESIS_SYSTEM_PROMPT + "\n\n参考资料：\n" + context,
                temperature=0.7,
                max_tokens=2048,
            )

            return answer.strip()

        except Exception as e:
            logger.warning(f"LLM semantic synthesis failed: {e}")
            return self._build_retrieval_text(retrieval_result)

    def _build_retrieval_context(self, result: RetrievalQueryResult, max_chunks: int = 5) -> str:
        """构造检索上下文"""
        if not result.success or not result.results:
            return "（无相关参考资料）"

        parts = []
        for i, r in enumerate(result.results[:max_chunks], 1):
            doc_name = r.get("document_name", "未知文档")
            text = r.get("text", "")
            parts.append(f"[{i}] 《{doc_name}》\n{text}")

        return "\n\n".join(parts)

    def _build_retrieval_text(self, result: RetrievalQueryResult) -> str:
        """直接生成检索结果文本（无 LLM 时）"""
        if not result.success or not result.results:
            return "未找到相关结果。"

        parts = ["找到以下相关内容：\n"]
        for i, r in enumerate(result.results[:5], 1):
            doc_name = r.get("document_name", "未知文档")
            text = r.get("text", "")[:200]
            parts.append(f"**{i}. {doc_name}**\n{text}...\n")

        return "\n".join(parts)

    # ------------------------------------------------------------------
    # 混合模式合成
    # ------------------------------------------------------------------

    async def _synthesize_hybrid(
        self,
        query: str,
        plan: QueryPlan,
        sql_result: SQLQueryResult,
        retrieval_result: RetrievalQueryResult,
    ) -> str:
        """混合模式：SQL 结果 + 语义检索结果"""
        llm = self._get_llm()
        if llm is None:
            # 没有 LLM，SQL 结果优先展示
            sql_answer = self._rule_based_sql_answer(query, plan, sql_result)
            return sql_answer

        try:
            sql_table = sql_result.to_markdown_table(max_rows=15)
            retrieval_context = self._build_retrieval_context(retrieval_result, max_chunks=3)

            context = f"""精确查询结果（来自数据表）：
{sql_table}

相关参考资料（语义检索）：
{retrieval_context}"""

            answer = await self._llm_text(
                prompt=f"用户问题：{query}",
                system_prompt=SYNTHESIS_SYSTEM_PROMPT + "\n\n" + context,
                temperature=0.5,
                max_tokens=2048,
            )

            return answer.strip()

        except Exception as e:
            logger.warning(f"LLM hybrid synthesis failed: {e}")
            return self._rule_based_sql_answer(query, plan, sql_result)

    # ------------------------------------------------------------------
    # 失败回答
    # ------------------------------------------------------------------

    def _synthesize_failure(
        self,
        query: str,
        plan: QueryPlan,
        sql_result: Optional[SQLQueryResult],
        retrieval_result: Optional[RetrievalQueryResult],
    ) -> str:
        """生成失败回答"""
        reasons = []

        if sql_result and not sql_result.success:
            reasons.append(f"数据查询失败：{sql_result.error}")

        if retrieval_result and not retrieval_result.success:
            reasons.append(f"语义检索失败：{retrieval_result.error}")

        if reasons:
            return "抱歉，未能获取到相关数据。\n\n原因：\n- " + "\n- ".join(reasons)

        return "根据现有资料，无法找到相关结果。请尝试换一种问法，或者确认数据是否存在。"

    # ------------------------------------------------------------------
    # 引用生成
    # ------------------------------------------------------------------

    def _build_references(
        self,
        sql_result: Optional[SQLQueryResult],
        retrieval_result: Optional[RetrievalQueryResult],
        context: Optional[AgentContext],
    ) -> list[dict]:
        """生成引用来源列表"""
        references = []

        # SQL 结果作为引用（如果有文档信息）
        if sql_result and sql_result.success and context:
            # 从上下文中获取文档信息
            doc_name = ""
            doc_id = ""
            if hasattr(context, "debug_info") and context.debug_info:
                doc_name = context.debug_info.get("file_name", "")
                doc_id = context.debug_info.get("document_id", "")

            if doc_name:
                references.append({
                    "chunk_id": "sql_query_result",
                    "document_id": doc_id,
                    "document_name": doc_name,
                    "chunk_index": 0,
                    "text": f"SQL查询结果，共 {sql_result.row_count} 行数据",
                    "score": 1.0,
                    "page_num": None,
                    "source_type": "sql",
                })

        # 向量检索结果作为引用
        if retrieval_result and retrieval_result.success:
            for r in retrieval_result.results[:5]:
                references.append({
                    "chunk_id": r.get("chunk_id", ""),
                    "document_id": r.get("document_id", ""),
                    "document_name": r.get("document_name", ""),
                    "chunk_index": r.get("chunk_index", 0),
                    "text": r.get("text", ""),
                    "score": r.get("score", 0.0),
                    "page_num": r.get("page_num"),
                    "source_type": "vector",
                })

        return references
