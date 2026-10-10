"""
上下文窗口管理器（Context Window Manager）

核心职责：
- TokenBudgetComposedStrategy（预算组合策略）：在一个 Token 预算内按顺序执行多个策略
- 优先级：工具结果裁剪 → 摘要替换 → 滑动窗口 → 紧急截断
- 分层历史：远期摘要 → 中期摘要 → 近期完整消息
- 关键轮次锚定（Key-turn Anchoring）：重要消息不被摘要吞掉

策略执行顺序（从温和到激进）：
  1. ToolResultPruning（工具结果裁剪） - 性价比最高，几乎不损失语义
  2. Summarization（摘要替换） - 对较早对话做摘要，保留语义
  3. SlidingWindow（滑动窗口） - 只保留最近 N 轮，兜底手段
  4. Truncation（紧急截断） - 最后安全网，保证 LLM 调用能发出去

组装顺序（发给模型的 prompt 顺序）：
  1. system_prompt（含检索上下文）
  2. [far_summary] 远期摘要（如果有）
  3. [mid_summary] 中期摘要（如果有）
  4. [near_summary] 近期滚动摘要（如果有）
  5. 近期完整消息（滑动窗口内的 user+assistant 对）
  6. 当前用户问题
"""

from dataclasses import dataclass, field

from app.core.config import settings
from app.core.logging import get_logger
from app.providers.llm.base import ChatMessage
from app.utils.token_utils import count_tokens

logger = get_logger(__name__)


@dataclass
class SummaryBundle:
    """分层摘要包"""

    far: str | None = None  # 远期（粗粒度）
    mid: str | None = None  # 中期（中粒度）
    near: str | None = None  # 近期（细粒度滚动摘要）


@dataclass
class HistoryMessage:
    """历史消息（内部表示）"""

    seq: int
    turn_id: int
    role: str
    content: str
    token_count: int = 0
    is_key_turn: bool = False
    tool_name: str | None = None  # 工具调用名（用于工具结果裁剪识别）
    is_tool_result: bool = False  # 是否为工具结果消息


@dataclass
class ContextAssemblyResult:
    """上下文组装结果"""

    messages: list[ChatMessage]  # 最终发给模型的消息列表
    total_tokens: int  # 总 token 数
    recent_start_seq: int  # 近期完整消息起始 seq
    used_summary_levels: list[str]  # 实际使用的摘要层级
    applied_strategies: list[str] = field(default_factory=list)  # 实际应用的策略


@dataclass
class StrategyResult:
    """单个策略的执行结果"""

    messages: list[dict]  # 处理后的消息列表
    tokens_saved: int  # 节省的 token 数
    strategy_name: str  # 策略名称


class BaseCompactionStrategy:
    """压缩策略基类"""

    name: str = "base"

    def apply(
        self,
        messages: list[dict],
        budget: int,
        current_tokens: int,
    ) -> StrategyResult:
        """应用策略

        Args:
            messages: 历史消息列表
            budget: 可用 token 预算
            current_tokens: 当前消息总 token 数

        Returns:
            StrategyResult
        """
        raise NotImplementedError


class ToolResultPruningStrategy(BaseCompactionStrategy):
    """第一优先级：工具结果裁剪（Tool Result Pruning）

    性价比最高的一步。大量 Token 往往消耗在原始工具调用输出上。
    策略：只保留最近几次的工具调用详情，将超长的历史工具结果进行软裁剪
    （保留头尾，如各 1500 字符）或硬清除（替换为占位符）。
    """

    name = "tool_result_pruning"

    def __init__(self, max_tool_result_tokens: int = 500, keep_recent: int = 3):
        self.max_tool_result_tokens = max_tool_result_tokens
        self.keep_recent = keep_recent  # 保留最近几条工具结果的完整内容

    def apply(
        self,
        messages: list[dict],
        budget: int,
        current_tokens: int,
    ) -> StrategyResult:
        if current_tokens <= budget:
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        result = []
        saved = 0
        tool_result_count = 0

        # 从后向前遍历，统计工具结果的位置
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].get("role") == "tool" or messages[i].get("is_tool_result"):
                tool_result_count += 1

        # 正序处理：最近 N 条工具结果保留完整，更早的进行裁剪
        recent_tool_index = 0
        for i in range(len(messages) - 1, -1, -1):
            m = messages[i]
            if m.get("role") == "tool" or m.get("is_tool_result"):
                recent_tool_index += 1
                if recent_tool_index > self.keep_recent:
                    content = m.get("content", "")
                    original_tokens = m.get("token_count") or count_tokens(content)
                    if original_tokens > self.max_tool_result_tokens:
                        # 软裁剪：保留头尾各约一半
                        half_chars = (self.max_tool_result_tokens * 2) // 2
                        if len(content) > half_chars * 2:
                            head = content[:half_chars]
                            tail = content[-half_chars:]
                            new_content = (
                                f"{head}\n...\n[工具结果已裁剪，"
                                f"原 {original_tokens} tokens，"
                                f"保留首尾各约 {self.max_tool_result_tokens // 2} tokens]\n...\n{tail}"
                            )
                            new_m = dict(m)
                            new_m["content"] = new_content
                            new_m["token_count"] = self.max_tool_result_tokens
                            new_m["_pruned"] = True
                            result.append(new_m)
                            saved += original_tokens - self.max_tool_result_tokens
                            continue
            result.append(m)

        # 恢复顺序
        result.reverse()

        if saved > 0:
            logger.debug(f"ToolResultPruning: saved {saved} tokens")
        return StrategyResult(messages=result, tokens_saved=saved, strategy_name=self.name)


class SummarizationStrategy(BaseCompactionStrategy):
    """第二优先级：摘要替换（Summarization）

    当裁剪工具结果后依然超标，就对"较早的一段对话"进行摘要。
    用已有的分层摘要（far/mid/near）替换原始多轮对话。
    摘要已由 HistoryCompactionService 预生成，这里只做组装决策。
    """

    name = "summarization"

    def __init__(self, summaries: SummaryBundle):
        self.summaries = summaries

    def apply(
        self,
        messages: list[dict],
        budget: int,
        current_tokens: int,
    ) -> StrategyResult:
        """用摘要替换较早的对话消息

        注意：这里我们不实时生成摘要（那是 compaction_service 的工作），
        而是根据已有的摘要层级，决定用哪些摘要替换哪些历史消息。
        """
        if current_tokens <= budget:
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        # 计算可用于摘要的 token 预算
        # 策略：尽可能放入更多摘要层级（从 far 开始，因为最省 token）
        # 然后保留较近的完整消息

        result_messages = []
        saved = 0

        # 1. 计算各层摘要的 token 数
        summary_candidates = []  # (level, text, tokens)
        if self.summaries.far:
            toks = count_tokens(self.summaries.far)
            summary_candidates.append(("far", self.summaries.far, toks))
        if self.summaries.mid:
            toks = count_tokens(self.summaries.mid)
            summary_candidates.append(("mid", self.summaries.mid, toks))
        if self.summaries.near:
            toks = count_tokens(self.summaries.near)
            summary_candidates.append(("near", self.summaries.near, toks))

        if not summary_candidates:
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        # 2. 找出关键轮次（锚定，不被摘要替换）
        key_turn_seqs = set()
        key_turn_ids = set()
        for m in messages:
            if m.get("is_key_turn"):
                key_turn_seqs.add(m["seq"])
                key_turn_ids.add(m.get("turn_id", 0))

        # 3. 按轮次分组
        turns = self._group_by_turn(messages)

        # 4. 贪心：从最早的轮次开始，用摘要替换，直到满足预算或摘要用完
        # 先确定：保留多少轮近期对话（至少保留 2 轮）
        min_recent_turns = 2
        max_summary_turn_index = len(turns) - min_recent_turns

        if max_summary_turn_index <= 0:
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        # 计算用摘要能替换多少轮
        # 简单策略：用所有可用摘要替换前半部分轮次
        # 更细的策略会在 SlidingWindow 中处理

        # 这里我们只是标记：如果有摘要，就用摘要替换最早的一批轮次
        # 具体替换多少由后续滑动窗口决定

        # 计算摘要总 token
        total_summary_tokens = sum(t for _, _, t in summary_candidates)

        # 计算最早的 N 轮的 token
        # 尝试用摘要替换最早的一半轮次
        replace_count = max(1, max_summary_turn_index // 2)
        replaced_tokens = sum(
            sum(m.get("token_count") or count_tokens(m.get("content", "")) for m in msgs)
            for _, msgs in turns[:replace_count]
        )

        if replaced_tokens > total_summary_tokens:
            # 用摘要替换确实能省 token，执行替换
            # 构造摘要消息（用一条 user 消息承载摘要上下文）
            summary_text_parts = []
            for level, text, _ in summary_candidates:
                label = {"far": "更早的对话", "mid": "中期对话", "near": "近期对话"}.get(
                    level, level
                )
                summary_text_parts.append(f"[{label}摘要]\n{text}")

            summary_block = "\n\n".join(summary_text_parts)

            # 用摘要消息 + 剩余轮次组成新的消息列表
            # 摘要作为一条特殊的"上下文"消息
            summary_msg = {
                "seq": -1,
                "turn_id": -1,
                "role": "system",  # 用 system 角色承载摘要，更自然
                "content": f"以下是此前对话的摘要供你参考：\n{summary_block}",
                "token_count": total_summary_tokens,
                "is_key_turn": False,
                "_is_summary": True,
            }
            result_messages = [summary_msg]

            # 添加未被替换的轮次（从 replace_count 开始）
            for _, msgs in turns[replace_count:]:
                result_messages.extend(msgs)

            saved = replaced_tokens - total_summary_tokens
            logger.debug(f"Summarization: saved {saved} tokens, replaced {replace_count} turns")
            return StrategyResult(
                messages=result_messages, tokens_saved=saved, strategy_name=self.name
            )

        return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

    def _group_by_turn(self, messages: list[dict]) -> list[tuple[int, list[dict]]]:
        """按 turn_id 分组"""
        turns = []
        current_turn = None
        current_msgs = []
        for m in messages:
            tid = m.get("turn_id", 0)
            if tid != current_turn:
                if current_msgs:
                    turns.append((current_turn, current_msgs))
                current_turn = tid
                current_msgs = [m]
            else:
                current_msgs.append(m)
        if current_msgs:
            turns.append((current_turn, current_msgs))
        return turns


class SlidingWindowStrategy(BaseCompactionStrategy):
    """第三优先级：滑动窗口（Sliding Window）

    如果摘要后 Token 还是超，就启用滑动窗口。
    只保留最近的 N 轮对话（例如最近 10-20 轮），直接丢弃更早的记录。
    这是保证系统不崩溃的兜底手段。

    保护规则：
    - System 消息永不丢弃（但我们的历史里一般没有 system）
    - 关键轮次锚定：被标记为 is_key_turn 的轮次优先保留
    - 按轮次裁剪，不切碎一轮 user+assistant
    """

    name = "sliding_window"

    def __init__(self, min_turns: int = 2, default_turns: int = 10):
        self.min_turns = min_turns  # 最少保留的轮次
        self.default_turns = default_turns  # 默认保留轮数

    def apply(
        self,
        messages: list[dict],
        budget: int,
        current_tokens: int,
    ) -> StrategyResult:
        if current_tokens <= budget:
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        # 按轮次分组
        turns = self._group_by_turn(messages)

        if len(turns) <= self.min_turns:
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        # 找出关键轮次
        key_turn_indices = set()
        for i, (tid, msgs) in enumerate(turns):
            if any(m.get("is_key_turn") for m in msgs):
                key_turn_indices.add(i)

        # 策略：从后向前累加 token，直到超过预算
        # 同时保证：
        # 1. 至少保留 min_turns 轮
        # 2. 关键轮次尽可能保留

        selected_indices = []
        total_toks = 0

        # 先保证最近的 min_turns 轮一定在
        for i in range(len(turns) - 1, max(-1, len(turns) - self.min_turns - 1), -1):
            turn_toks = sum(
                m.get("token_count") or count_tokens(m.get("content", "")) for m in turns[i][1]
            )
            selected_indices.append(i)
            total_toks += turn_toks

        # 然后从近到远尝试加入更多轮次（优先关键轮次）
        # 先加关键轮次
        for i in range(len(turns) - self.min_turns - 1, -1, -1):
            if i in key_turn_indices:
                turn_toks = sum(
                    m.get("token_count") or count_tokens(m.get("content", "")) for m in turns[i][1]
                )
                if total_toks + turn_toks <= budget:
                    selected_indices.append(i)
                    total_toks += turn_toks

        # 再加普通轮次
        for i in range(len(turns) - self.min_turns - 1, -1, -1):
            if i not in selected_indices and i not in key_turn_indices:
                turn_toks = sum(
                    m.get("token_count") or count_tokens(m.get("content", "")) for m in turns[i][1]
                )
                if total_toks + turn_toks <= budget:
                    selected_indices.append(i)
                    total_toks += turn_toks
                else:
                    break  # 从近到远，一旦放不下就停止

        if len(selected_indices) == len(turns):
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        # 构造结果（按原始顺序）
        selected_set = set(selected_indices)
        result = []
        for i, (_, msgs) in enumerate(turns):
            if i in selected_set:
                result.extend(msgs)

        saved = current_tokens - total_toks
        dropped_turns = len(turns) - len(selected_indices)
        logger.debug(
            f"SlidingWindow: saved {saved} tokens, "
            f"dropped {dropped_turns}/{len(turns)} turns, "
            f"keeping {len(selected_indices)} turns"
        )
        return StrategyResult(messages=result, tokens_saved=saved, strategy_name=self.name)

    def _group_by_turn(self, messages: list[dict]) -> list[tuple[int, list[dict]]]:
        """按 turn_id 分组"""
        turns = []
        current_turn = None
        current_msgs = []
        for m in messages:
            tid = m.get("turn_id", 0)
            if tid != current_turn:
                if current_msgs:
                    turns.append((current_turn, current_msgs))
                current_turn = tid
                current_msgs = [m]
            else:
                current_msgs.append(m)
        if current_msgs:
            turns.append((current_turn, current_msgs))
        return turns


class TruncationStrategy(BaseCompactionStrategy):
    """第四优先级：紧急截断（Truncation）

    作为最后的安全网，如果连滑动窗口都无法满足预算（比如单条消息就超长），
    就执行最粗暴的按最旧优先截断，保证 LLM 调用能发出去。

    保护规则：
    - 最新的一条用户消息永不截断（但可以截断内容的前面部分）
    - 从最早的消息开始整条丢弃
    - 如果单条消息超长，截断其内容的前面部分
    """

    name = "truncation"

    def __init__(self, min_messages: int = 2):
        self.min_messages = min_messages  # 最少保留消息数

    def apply(
        self,
        messages: list[dict],
        budget: int,
        current_tokens: int,
    ) -> StrategyResult:
        if current_tokens <= budget:
            return StrategyResult(messages=messages, tokens_saved=0, strategy_name=self.name)

        result = list(messages)
        saved = 0

        # 第一步：从最旧的消息开始整条丢弃，直到至少满足预算或只剩 min_messages
        while len(result) > self.min_messages:
            first_msg = result[0]
            msg_tokens = first_msg.get("token_count") or count_tokens(first_msg.get("content", ""))
            if current_tokens - saved - msg_tokens <= budget:
                # 再丢一条就够了，但为了安全先看看
                break
            result = result[1:]
            saved += msg_tokens

        # 重新计算
        remaining_tokens = current_tokens - saved

        # 第二步：如果还是超，且单条消息超长，截断最早的那条的内容前面部分
        if remaining_tokens > budget and len(result) > 0:
            # 找最早的一条长消息
            for i in range(len(result) - 1):  # 最后一条不截（通常是用户最新消息）
                msg = result[i]
                msg_tokens = msg.get("token_count") or count_tokens(msg.get("content", ""))
                if msg_tokens > 100:  # 太短的不用截
                    # 计算需要截掉多少 token
                    excess = remaining_tokens - budget
                    # 按比例估算字符数
                    content = msg.get("content", "")
                    chars_per_token = max(1, len(content) // max(1, msg_tokens))
                    chars_to_remove = excess * chars_per_token

                    if chars_to_remove < len(content) - 100:
                        # 截断前面部分，保留尾部
                        new_content = content[chars_to_remove:]
                        new_content = f"[消息前面部分已截断]\n{new_content}"
                        new_tokens = count_tokens(new_content)
                        new_msg = dict(msg)
                        new_msg["content"] = new_content
                        new_msg["token_count"] = new_tokens
                        new_msg["_truncated"] = True
                        result[i] = new_msg
                        saved += msg_tokens - new_tokens
                        remaining_tokens = current_tokens - saved
                        break

        logger.warning(
            f"Truncation applied: saved {saved} tokens, "
            f"{len(messages)} -> {len(result)} messages, "
            f"final ~{current_tokens - saved} tokens (budget: {budget})"
        )
        return StrategyResult(messages=result, tokens_saved=saved, strategy_name=self.name)


class TokenBudgetComposedStrategy:
    """Token 预算组合策略（Composed Strategy）

    在一个 Token 预算内，按优先级顺序执行多个策略，达到预算即停止。

    执行顺序：
    1. ToolResultPruning - 工具结果裁剪（最省 token，几乎不损语义）
    2. Summarization - 摘要替换（用预生成摘要替换较早对话）
    3. SlidingWindow - 滑动窗口（只保留最近 N 轮）
    4. Truncation - 紧急截断（最后安全网）

    设计原则：
    - 确定性：相同输入产生相同输出
    - 可审计：记录每个策略的应用情况和节省的 token
    - 渐进式：先尝试温和手段，必要时才用激进手段
    - 保护规则：system/关键消息/最新消息优先保护
    """

    def __init__(
        self,
        summaries: SummaryBundle,
        tool_max_tokens: int = 500,
        tool_keep_recent: int = 3,
        sliding_min_turns: int = 2,
        trunc_min_messages: int = 2,
    ):
        self.strategies: list[BaseCompactionStrategy] = [
            ToolResultPruningStrategy(
                max_tool_result_tokens=tool_max_tokens,
                keep_recent=tool_keep_recent,
            ),
            SummarizationStrategy(summaries),
            SlidingWindowStrategy(min_turns=sliding_min_turns),
            TruncationStrategy(min_messages=trunc_min_messages),
        ]

    def apply(
        self,
        messages: list[dict],
        budget: int,
    ) -> tuple[list[dict], list[str]]:
        """按顺序执行策略，直到满足预算

        Args:
            messages: 历史消息列表
            budget: 可用 token 预算

        Returns:
            (处理后的消息列表, 应用的策略名称列表)
        """
        if not messages:
            return [], []

        current_tokens = sum(
            m.get("token_count") or count_tokens(m.get("content", "")) for m in messages
        )

        if current_tokens <= budget:
            return messages, []

        applied = []
        result = list(messages)

        for strategy in self.strategies:
            current_tokens = sum(
                m.get("token_count") or count_tokens(m.get("content", "")) for m in result
            )
            if current_tokens <= budget:
                break

            strategy_result = strategy.apply(result, budget, current_tokens)
            if strategy_result.tokens_saved > 0:
                result = strategy_result.messages
                applied.append(strategy.name)
                logger.debug(
                    f"Strategy '{strategy.name}' applied, "
                    f"saved {strategy_result.tokens_saved} tokens, "
                    f"remaining ~{current_tokens - strategy_result.tokens_saved} tokens"
                )

        return result, applied


class ContextWindowManager:
    """上下文窗口管理器

    Token 预算分配（从 chat_context_window_tokens 总预算中）：
    - 系统提示 + 检索上下文：chat_context_reserve_tokens 预留
    - 历史消息（摘要 + 近期完整）：剩余 token 预算

    使用 TokenBudgetComposedStrategy 进行历史消息压缩。
    """

    def __init__(
        self,
        window_tokens: int | None = None,
        reserve_tokens: int | None = None,
        recent_turns: int | None = None,
    ):
        self.window_tokens = window_tokens or settings.chat_context_window_tokens
        self.reserve_tokens = reserve_tokens or settings.chat_context_reserve_tokens
        self.recent_turns = recent_turns or settings.chat_recent_turns
        # 历史消息可用 token 预算
        self.history_budget = max(500, self.window_tokens - self.reserve_tokens)
        logger.debug(
            f"Context window: total={self.window_tokens}, "
            f"reserve={self.reserve_tokens}, history_budget={self.history_budget}"
        )

    def assemble(
        self,
        system_prompt: str,
        current_query: str,
        history_messages: list[dict],
        summaries: SummaryBundle,
        key_turn_seqs: list[int] | None = None,
        extra_system_tokens: int = 0,
    ) -> ContextAssemblyResult:
        """组装发给模型的消息列表

        Args:
            system_prompt: 系统提示（含检索上下文）
            current_query: 当前用户问题
            history_messages: 历史消息列表（按 seq 升序），每项包含 role/content/seq/token_count/is_key_turn
            summaries: 分层摘要
            key_turn_seqs: 关键轮次 seq 列表（额外锚定，不被裁掉）
            extra_system_tokens: 额外追加进 system prompt 的内容（如历史召回/长期记忆）
                所需的 token 数，从历史预算中扣除，避免总输入越过上下文窗口

        Returns:
            ContextAssemblyResult
        """
        key_turn_seqs = set(key_turn_seqs or [])
        # 标记 is_key_turn 的消息也加入锚定集合
        for m in history_messages:
            if m.get("is_key_turn"):
                key_turn_seqs.add(m["seq"])

        system_tokens = count_tokens(system_prompt)
        query_tokens = count_tokens(current_query)

        # 剩余可用 token = 历史预算 - query_tokens - 额外 system 内容（system 走 reserve）
        available = self.history_budget - query_tokens - max(0, extra_system_tokens)
        if available < 200:
            available = 200  # 至少留 200 token 给历史

        # 使用 TokenBudgetComposedStrategy 压缩历史消息
        composed = TokenBudgetComposedStrategy(
            summaries=summaries,
            tool_max_tokens=settings.chat_tool_result_max_tokens,
            tool_keep_recent=3,
            sliding_min_turns=2,
            trunc_min_messages=2,
        )

        compressed_history, applied_strategies = composed.apply(history_messages, available)

        # ---- 构造最终消息列表 ----
        messages: list[ChatMessage] = []

        # 1. system prompt
        messages.append(ChatMessage("system", system_prompt))

        # 2. 添加压缩后的历史消息
        used_summary_levels: list[str] = []
        for m in compressed_history:
            if m.get("_is_summary"):
                # 摘要消息已经是 system 角色的内容了
                # 但我们已有一条 system 消息，所以合并进去或者单独作为 system
                # 这里选择追加到 system prompt 末尾更干净
                # 但为了兼容旧逻辑，我们用单独的消息
                if m.get("role") == "system":
                    # 如果摘要标记为 system，合并到第一条 system 消息
                    messages[0] = ChatMessage(
                        "system",
                        messages[0].content + "\n\n" + m["content"],
                    )
                else:
                    messages.append(ChatMessage(m["role"], m["content"]))
                # 标记使用了摘要
                if "far" in m.get("content", ""):
                    if "far" not in used_summary_levels:
                        used_summary_levels.append("far")
                if "mid" in m.get("content", ""):
                    if "mid" not in used_summary_levels:
                        used_summary_levels.append("mid")
                if "near" in m.get("content", ""):
                    if "near" not in used_summary_levels:
                        used_summary_levels.append("near")
            else:
                messages.append(ChatMessage(m["role"], m["content"]))

        # 如果没使用摘要策略，但有摘要可用，也记录一下
        if "summarization" not in applied_strategies:
            # 检查摘要是否实际被包含
            if summaries.far and "far" not in used_summary_levels:
                pass  # 摘要未被使用
        else:
            # 已使用摘要策略
            if summaries.far and "far" not in used_summary_levels:
                used_summary_levels.append("far")
            if summaries.mid and "mid" not in used_summary_levels:
                used_summary_levels.append("mid")
            if summaries.near and "near" not in used_summary_levels:
                used_summary_levels.append("near")

        # 3. 当前问题
        messages.append(ChatMessage("user", current_query))

        # 计算总 token
        total_tokens = system_tokens + query_tokens
        for m in compressed_history:
            total_tokens += m.get("token_count") or count_tokens(m.get("content", ""))

        recent_start_seq = compressed_history[0]["seq"] if compressed_history else 0

        result = ContextAssemblyResult(
            messages=messages,
            total_tokens=total_tokens,
            recent_start_seq=recent_start_seq,
            used_summary_levels=used_summary_levels,
            applied_strategies=applied_strategies,
        )
        logger.debug(
            f"Context assembled: {total_tokens} tokens, "
            f"history_msgs={len(compressed_history)}, "
            f"summaries={used_summary_levels}, "
            f"strategies={applied_strategies}"
        )
        return result

    def needs_compaction(
        self,
        history_tokens: int,
    ) -> bool:
        """判断是否需要触发压缩（滚动摘要）

        当历史消息 token 数超过预算 * 阈值时触发。
        """
        threshold = self.history_budget * settings.chat_compaction_threshold
        return history_tokens > threshold

    # ------------------------------------------------------------------
    # 历史文本块（供不组装 messages 的生成路径使用，如 SmartQuery 合成）
    # ------------------------------------------------------------------

    def build_history_block(
        self,
        history_messages: list[dict],
        summaries: SummaryBundle,
        current_query: str = "",
        extra_system_tokens: int = 0,
    ) -> tuple[str, list[str]]:
        """把对话历史压缩/蒸馏/丢弃后，渲染成一段可直接注入 prompt 的文本块。

        与 assemble() 共用同一套 TokenBudgetComposedStrategy，保证两条生成路径
        （常规 RAG / SmartQuery 合成）的上下文策略完全一致：

          1. 压缩：工具结果裁剪（ToolResultPruning）
          2. 蒸馏：分层摘要替换（Summarization，far/mid/near）
          3. 丢弃：滑动窗口丢最旧轮次（SlidingWindow）→ 紧急截断（Truncation）

        Args:
            history_messages: 历史消息（按 seq 升序，含 role/content/token_count/seq/turn_id）
            summaries: 分层摘要包
            current_query: 当前问题（用于扣除 query 占用的 token）
            extra_system_tokens: 额外注入的 system 内容（召回/长期记忆）token 数，从预算中扣除

        Returns:
            (history_text, applied_strategies)
            history_text 为空字符串表示无可用历史。
        """
        if not history_messages:
            return "", []

        # 与 assemble() 一致的预算口径：历史预算 - 当前问题占用 - 额外 system 内容
        available = self.history_budget - count_tokens(current_query) - max(0, extra_system_tokens)
        if available < 200:
            available = 200

        composed = TokenBudgetComposedStrategy(
            summaries=summaries,
            tool_max_tokens=settings.chat_tool_result_max_tokens,
            tool_keep_recent=3,
            sliding_min_turns=2,
            trunc_min_messages=2,
        )
        compressed, applied = composed.apply(history_messages, available)

        parts: list[str] = []
        for m in compressed:
            if m.get("_is_summary"):
                parts.append(f"【历史摘要】{m.get('content', '')}")
                continue
            role = m.get("role")
            if role == "user":
                parts.append(f"用户：{m.get('content', '')}")
            elif role == "assistant":
                parts.append(f"助手：{m.get('content', '')}")
            # tool / system 等内部消息不进对话历史块

        if not parts:
            return "", applied

        logger.debug(
            f"History block built: msgs={len(compressed)}, applied={applied}, "
            f"chars={sum(len(p) for p in parts)}"
        )
        return "\n".join(parts), applied
