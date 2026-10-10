"""
对话历史压缩服务（History Compaction Service）

实现技术：
- 分层摘要（Hierarchical Summarization）：far / mid / near 三层
- 滚动摘要（Rolling / Incremental Summarization）：只把新挤出窗口的部分增量摘要
- 关键轮次锚定（Key-turn Anchoring）：标记重要消息，摘要时保留要点
- 工具结果裁剪（Tool Result Truncation）：优先压缩/截断旧的工具输出

触发时机：
- 每次对话结束后，检查历史 token 是否超过阈值
- 超过则触发压缩：把超出滑动窗口的部分增量地合并进 near 摘要
- 当 near 摘要超过一定长度，合并进 mid
- 当 mid 超过一定长度，合并进 far（远期归档）
"""

from typing import List, Tuple, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.repositories.conversation_repo import ConversationRepository
from app.services.context_manager import SummaryBundle
from app.providers.llm.factory import get_summary_llm
from app.utils.token_utils import count_tokens

logger = get_logger(__name__)

# 各层摘要的最大 token 阈值（超过则升级合并到上一层）
NEAR_SUMMARY_MAX_TOKENS = 800
MID_SUMMARY_MAX_TOKENS = 600


class HistoryCompactionService:
    """历史压缩服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.repo = ConversationRepository(db)

    async def get_summaries(self, conversation_id: str) -> SummaryBundle:
        """获取当前所有层的摘要（供上下文组装使用）"""
        summaries = await self.repo.get_all_summaries(conversation_id)
        bundle = SummaryBundle()
        for s in summaries:
            if s.level == "far":
                bundle.far = s.content
            elif s.level == "mid":
                bundle.mid = s.content
            elif s.level == "near":
                bundle.near = s.content
        return bundle

    async def maybe_compact(
        self,
        conversation_id: str,
        session_id: str,
        history_messages: List[dict],
    ) -> Tuple[bool, str]:
        """按需触发压缩

        Args:
            conversation_id: 会话 DB ID
            session_id: 会话对外 ID
            history_messages: 当前完整历史消息列表（dict 形式，按 seq 升序）

        Returns:
            (did_compact, level) - 是否执行了压缩，压缩到哪一层
        """
        history_tokens = sum(m.get("token_count", count_tokens(m["content"])) for m in history_messages)
        budget = (
            settings.chat_context_window_tokens - settings.chat_context_reserve_tokens
        )
        threshold = budget * settings.chat_compaction_threshold

        if history_tokens <= threshold:
            return False, "none"

        # 需要压缩：计算哪些消息已经"挤出"了近期窗口
        # 近期窗口 = 最近 recent_turns 轮
        recent_turns = settings.chat_recent_turns
        # 找到最近 N 轮之前的消息
        turns = self._group_by_turn(history_messages)
        if len(turns) <= recent_turns:
            return False, "none"

        # 被挤出的轮次（前面的部分）
        overflow_turns = turns[:-recent_turns]
        if not overflow_turns:
            return False, "none"

        # 工具结果裁剪：先对工具消息做截断
        overflow_msgs = []
        for _, msgs in overflow_turns:
            overflow_msgs.extend(msgs)

        overflow_msgs = self._truncate_tool_results(overflow_msgs)

        # 生成增量摘要（只摘要被挤出的部分）
        try:
            near_summary = await self._incremental_near_summary(
                conversation_id, overflow_msgs
            )
        except Exception as e:
            logger.warning(f"Near summary generation failed: {e}")
            return False, "none"

        # 检查 near 摘要大小，必要时升级到 mid
        level = "near"
        if count_tokens(near_summary) > NEAR_SUMMARY_MAX_TOKENS:
            try:
                await self._promote_near_to_mid(conversation_id)
                level = "mid"
            except Exception as e:
                logger.warning(f"Near -> Mid promotion failed: {e}")

        # 检查 mid 摘要大小，必要时升级到 far
        mid = await self.repo.get_summary(conversation_id, "mid")
        if mid and count_tokens(mid.content) > MID_SUMMARY_MAX_TOKENS:
            try:
                await self._promote_mid_to_far(conversation_id)
                level = "far"
            except Exception as e:
                logger.warning(f"Mid -> Far promotion failed: {e}")

        logger.info(
            f"Compaction done for session {session_id}: "
            f"history_tokens={history_tokens}, level={level}"
        )
        return True, level

    # ---------- 内部方法 ----------

    def _group_by_turn(self, messages: List[dict]) -> List[Tuple[int, List[dict]]]:
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

    def _truncate_tool_results(self, messages: List[dict]) -> List[dict]:
        """工具结果裁剪：旧的 tool 消息内容超长则截断"""
        max_toks = settings.chat_tool_result_max_tokens
        result = []
        for m in messages:
            if m.get("role") == "tool" and m.get("content"):
                toks = count_tokens(m["content"])
                if toks > max_toks:
                    # 粗略截断：按字符数截取（中文按 1:1，英文按 4:1，这里保守按 2:1）
                    chars = max_toks * 2
                    truncated = m["content"][:chars] + f"...（已截断，原 {toks} tokens）"
                    m = dict(m)
                    m["content"] = truncated
                    m["token_count"] = max_toks
            result.append(m)
        return result

    async def _incremental_near_summary(
        self,
        conversation_id: str,
        new_messages: List[dict],
    ) -> str:
        """增量更新 near 摘要

        把新挤出窗口的消息，与原 near 摘要合并，生成新的 near 摘要。
        """
        if not new_messages:
            return ""

        # 取出原 near 摘要
        existing = await self.repo.get_summary(conversation_id, "near")
        existing_content = existing.content if existing else ""
        existing_end_seq = existing.end_seq if existing else -1

        # 新消息文本
        new_text = "\n".join(
            [f"{m['role']}: {m['content']}" for m in new_messages]
        )
        new_start_seq = min(m["seq"] for m in new_messages)
        new_end_seq = max(m["seq"] for m in new_messages)

        # 生成摘要
        if existing_content:
            prompt = (
                "以下是一段对话的既有摘要和新增的对话内容，请更新摘要，"
                "保持关键信息，去除冗余，控制在 300 字以内。\n\n"
                f"既有摘要：\n{existing_content}\n\n"
                f"新增对话：\n{new_text}\n\n"
                "请直接输出更新后的摘要，不要加前缀解释。"
            )
        else:
            prompt = (
                "请将以下对话内容总结成一段 200-300 字的摘要，"
                "保留关键信息、用户需求、重要结论和决策点。\n\n"
                f"对话内容：\n{new_text}\n\n"
                "请直接输出摘要，不要加前缀解释。"
            )

        llm = get_summary_llm()
        summary_text = await llm.simple_chat(
            prompt=prompt,
            system_prompt="你是一个专业的对话摘要助手，擅长提炼关键信息。",
            temperature=0.3,
            max_tokens=1200,
        )
        summary_text = summary_text.strip()

        # 写回数据库
        start_seq = min(existing_end_seq + 1, new_start_seq) if existing else new_start_seq
        end_seq = max(existing_end_seq, new_end_seq) if existing else new_end_seq
        await self.repo.upsert_summary(
            conversation_id, "near", summary_text, start_seq, end_seq
        )

        return summary_text

    async def _promote_near_to_mid(self, conversation_id: str) -> None:
        """将 near 摘要合并进 mid 摘要，然后清空 near"""
        near = await self.repo.get_summary(conversation_id, "near")
        if not near:
            return

        mid = await self.repo.get_summary(conversation_id, "mid")

        if mid:
            # 合并 mid + near
            prompt = (
                "以下是两段对话摘要（中期摘要 + 近期摘要），请合并成一段中期摘要，"
                "保留关键信息，控制在 400 字以内。\n\n"
                f"中期摘要：\n{mid.content}\n\n"
                f"近期摘要：\n{near.content}\n\n"
                "请直接输出合并后的摘要。"
            )
            start_seq = min(mid.start_seq, near.start_seq)
            end_seq = max(mid.end_seq, near.end_seq)
        else:
            prompt = (
                "请将以下近期对话摘要改写成更精炼的中期摘要（200-400字），"
                "保留关键结论和用户偏好。\n\n"
                f"近期摘要：\n{near.content}\n\n"
                "请直接输出中期摘要。"
            )
            start_seq = near.start_seq
            end_seq = near.end_seq

        llm = get_summary_llm()
        mid_text = await llm.simple_chat(
            prompt=prompt,
            system_prompt="你是一个专业的对话摘要助手。",
            temperature=0.3,
            max_tokens=1200,
        )
        mid_text = mid_text.strip()

        # 更新 mid
        await self.repo.upsert_summary(
            conversation_id, "mid", mid_text, start_seq, end_seq
        )

        # 清空 near（删除）
        # 这里不删除，而是重置为空（保留结构便于后续写入）
        await self.repo.upsert_summary(
            conversation_id, "near", "", end_seq + 1, end_seq
        )

    async def _promote_mid_to_far(self, conversation_id: str) -> None:
        """将 mid 摘要合并进 far 摘要，然后清空 mid"""
        mid = await self.repo.get_summary(conversation_id, "mid")
        if not mid or not mid.content:
            return

        far = await self.repo.get_summary(conversation_id, "far")

        if far:
            prompt = (
                "以下是两段对话摘要（远期摘要 + 中期摘要），请合并成一段远期摘要，"
                "最精炼地保留核心信息，控制在 300 字以内。\n\n"
                f"远期摘要：\n{far.content}\n\n"
                f"中期摘要：\n{mid.content}\n\n"
                "请直接输出合并后的远期摘要。"
            )
            start_seq = min(far.start_seq, mid.start_seq)
            end_seq = max(far.end_seq, mid.end_seq)
        else:
            prompt = (
                "请将以下中期对话摘要改写成高度精炼的远期摘要（150-300字），"
                "只保留最核心的背景、目标和结论。\n\n"
                f"中期摘要：\n{mid.content}\n\n"
                "请直接输出远期摘要。"
            )
            start_seq = mid.start_seq
            end_seq = mid.end_seq

        llm = get_summary_llm()
        far_text = await llm.simple_chat(
            prompt=prompt,
            system_prompt="你是一个专业的对话摘要助手。",
            temperature=0.3,
            max_tokens=1200,
        )
        far_text = far_text.strip()

        await self.repo.upsert_summary(
            conversation_id, "far", far_text, start_seq, end_seq
        )

        # 清空 mid
        await self.repo.upsert_summary(
            conversation_id, "mid", "", end_seq + 1, end_seq
        )
