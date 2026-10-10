"""记忆服务：会话内"按需历史召回" + 跨会话"长期用户记忆"。

对照三层记忆设计：
- 当前对话上下文：最近窗口 + 结构化状态（其它模块负责）
- 历史对话与摘要：分层摘要（compaction 负责）
- 按需历史召回：本模块 build_recall_block —— 只召回"最近窗口之外、且与当前问题相关"的旧消息
- 长期用户信息：本模块 UserMemoryService —— 跨会话保存用户偏好/背景并按相关性注入

设计原则：
- 召回/记忆都是"按需"的：命中触发词或有可用记忆才注入，绝不无脑塞历史
- 纯 Python 相关性打分（中文 bigram + 英文词），不额外调用 LLM（除长期记忆的抽取）
- 与"是否携带历史消息"解耦：两者互不影响
"""

from __future__ import annotations

import asyncio
import re

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# 文本相关性工具（中文 bigram + 英文/数字词）
# ---------------------------------------------------------------------------

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9_]{2,}")


def _grams(text: str) -> set[str]:
    t = text or ""
    grams = {w.lower() for w in _ASCII_TOKEN_RE.findall(t)}
    zh = re.sub(r"[^\u4e00-\u9fa5]", "", t)
    for i in range(max(0, len(zh) - 1)):
        grams.add(zh[i : i + 2])
    return grams


def _score(query_grams: set[str], doc: str) -> float:
    """查询词元在文档中的覆盖率（0-1）"""
    if not query_grams:
        return 0.0
    dg = _grams(doc)
    if not dg:
        return 0.0
    return len(query_grams & dg) / len(query_grams)


# ---------------------------------------------------------------------------
# A. 会话内按需历史召回（最近窗口之外）
# ---------------------------------------------------------------------------

# 引用/回溯触发词：出现这些词时才考虑召回更早的历史
RECALL_TRIGGERS = re.compile(
    r"继续|之前|刚才|上次|前面|早先|曾经|上面(说|提)|之前(说|提|提到)|前面(说|提)"
)


def _group_by_turn(messages: list[dict]) -> list[list[dict]]:
    turns: list[list[dict]] = []
    current: list[dict] = []
    last_tid = None
    for m in messages or []:
        tid = m.get("turn_id", 0)
        if last_tid is not None and tid != last_tid:
            if current:
                turns.append(current)
            current = []
        current.append(m)
        last_tid = tid
    if current:
        turns.append(current)
    return turns


def build_recall_block(
    history_messages: list[dict],
    query: str,
    recent_turns: int | None = None,
    top_k: int = 3,
    min_score: float = 0.15,
) -> str:
    """在"最近窗口之外"的历史消息里，召回与当前问题最相关的若干条。

    仅当命中回溯触发词、且确有高分旧消息时才返回文本块，否则返回空串（零副作用）。
    """
    if not getattr(settings, "chat_history_recall_enabled", True):
        return ""
    if not query or not history_messages:
        return ""
    if not RECALL_TRIGGERS.search(query):
        return ""

    recent = recent_turns or getattr(settings, "chat_recent_turns", 6)
    turns = _group_by_turn(history_messages)
    if len(turns) <= recent:
        return ""

    older_msgs = [m for group in turns[:-recent] for m in group]
    if not older_msgs:
        return ""

    q_grams = _grams(query)
    scored: list[tuple[float, dict]] = []
    for m in older_msgs:
        content = (m.get("content") or "").strip()
        if len(content) < 4:
            continue
        s = _score(q_grams, content)
        if s >= min_score:
            scored.append((s, m))

    if not scored:
        return ""

    scored.sort(key=lambda x: x[0], reverse=True)
    picked = [m for _, m in scored[:top_k]]
    picked.sort(key=lambda m: m.get("seq", 0))

    lines = []
    for m in picked:
        role = "用户" if m.get("role") == "user" else "助手"
        content = (m.get("content") or "").strip().replace("\n", " ")[:300]
        lines.append(f"{role}：{content}")
    block = "【历史回溯（更早的相关对话，仅供参考）】\n" + "\n".join(lines)
    logger.info(f"History recall: hits={len(picked)}, query='{query[:30]}...'")
    return block


# ---------------------------------------------------------------------------
# B. 跨会话长期用户记忆
# ---------------------------------------------------------------------------

# 只有用户显式表达"要长期记住"的意图时才抽取，避免每轮都调 LLM
MEMORY_TRIGGERS = re.compile(
    r"记住|请记住|以后都|之后都|今后都|我的偏好|我的习惯|我们公司|我一直|默认用|长期记得"
)

_EXTRACT_SYSTEM_PROMPT = """你是一个用户长期记忆抽取器。请从这轮对话中提取"值得长期记住的用户信息"
（用户偏好、业务背景、长期约束等）。要求：
1. 只提取用户明确表达的、跨会话仍然有效的信息；
2. 用一句话概括，不超过 40 字；
3. 若没有值得长期记住的信息，只输出：无
只输出结果本身，不要任何解释。"""


class UserMemoryService:
    """长期用户记忆（跨会话）"""

    def __init__(self, db):
        from app.repositories.conversation_repo import ConversationRepository

        self.db = db
        self.repo = ConversationRepository(db)
        self._llm = None
        # 后台任务引用集合（避免 fire-and-forget 任务被 GC）
        self._bg_tasks: set[asyncio.Task] = set()

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
                logger.warning(f"Failed to create LLM for UserMemoryService: {e}")
                self._llm = None
        return self._llm

    async def maybe_store(
        self, user_id: str | None, query: str, reply: str, conversation_id: str = ""
    ) -> None:
        """命中长期记忆意图时，抽取一条事实并入库（非阻塞关键路径，失败忽略）"""
        if not getattr(settings, "chat_user_memory_enabled", True):
            return
        if not user_id or not query:
            return
        if not MEMORY_TRIGGERS.search(query):
            return
        llm = self._get_llm()
        if llm is None:
            return
        try:
            raw = await llm.simple_chat(
                prompt=f"用户：{query}\n助手：{(reply or '')[:500]}\n\n请输出抽取结果：",
                system_prompt=_EXTRACT_SYSTEM_PROMPT,
                temperature=0.0,
                max_tokens=800,
            )
            fact = (raw or "").strip()
            if not fact or fact in ("无", "没有", "None") or len(fact) > 120:
                return
            await self.repo.add_user_memory(user_id, fact, conversation_id)
            logger.info(f"User memory stored: {fact[:40]}")
        except Exception as e:
            logger.warning(f"User memory extraction failed (non-critical): {e}")

    def schedule_store(
        self, user_id: Optional[str], query: str, reply: str, conversation_id: str = ""
    ) -> None:
        """命中长期记忆意图时，**后台异步**抽取并入库（不阻塞响应路径）。

        使用独立会话执行，避免请求级会话关闭后写入失败；早退条件与 maybe_store 一致。
        """
        if not getattr(settings, "chat_user_memory_enabled", True):
            return
        if not user_id or not query or not MEMORY_TRIGGERS.search(query):
            return
        try:
            task = asyncio.create_task(
                self._store_task(user_id, query, reply, conversation_id)
            )
        except RuntimeError as e:  # 无运行中的事件循环
            logger.warning(f"Schedule user memory store failed: {e}")
            return
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)

    async def _store_task(
        self, user_id: str, query: str, reply: str, conversation_id: str
    ) -> None:
        try:
            from app.core.chat_database import chat_async_session

            async with chat_async_session() as db:
                service = UserMemoryService(db)
                await service.maybe_store(user_id, query, reply, conversation_id)
                await db.commit()
        except Exception as e:
            logger.warning(f"Background user memory store failed (non-critical): {e}")

    async def build_block(
        self, user_id: str | None, query: str, top_k: int = 3, min_score: float = 0.08
    ) -> str:
        """取该用户长期记忆中与当前问题相关的若干条，拼成注入块"""
        if not getattr(settings, "chat_user_memory_enabled", True):
            return ""
        if not user_id:
            return ""
        try:
            memories = await self.repo.list_user_memories(user_id, limit=200)
        except Exception as e:
            logger.warning(f"Load user memories failed (non-critical): {e}")
            return ""
        if not memories:
            return ""

        q_grams = _grams(query)
        scored = []
        for m in memories:
            s = _score(q_grams, m.content)
            if s >= min_score:
                scored.append((s, m))
        if not scored:
            return ""
        # 稳定排序：同分时保留 list_user_memories 的"新→旧"顺序
        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [m for _, m in scored[:top_k]]
        block = "【长期记忆（用户画像/偏好，供参考）】\n" + "\n".join(
            f"- {m.content}" for m in picked
        )
        logger.info(f"User memory injected: {len(picked)} items for user {user_id[:8]}...")
        return block
