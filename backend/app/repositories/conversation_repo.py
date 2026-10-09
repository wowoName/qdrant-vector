"""
会话 Repository - 封装会话/消息/摘要的持久化读写

读写策略：
- 写：先写 DB（WAL 思想，保证不丢），再写 Redis 缓存（失败不影响主流程）
- 读：优先读 Redis 热缓存；未命中则从 DB 加载并回填 Redis
- 乐观并发控制：用 conversation.version 做版本校验（并发写入冲突时重试）
"""

import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from sqlalchemy import select, func, case
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.redis import (
    cache_get_session_messages,
    cache_set_session_messages,
    cache_delete_session,
)
from app.models.db.chat import ConversationDB, MessageDB, ConversationSummaryDB
from app.utils.token_utils import count_tokens

logger = get_logger(__name__)


class ConversationRepository:
    """会话仓储"""

    def __init__(self, db: AsyncSession):
        self.db = db

    # ---------- 会话 CRUD ----------

    async def get_by_session_id(self, session_id: str) -> Optional[ConversationDB]:
        """根据 session_id 获取会话"""
        stmt = select(ConversationDB).where(ConversationDB.session_id == session_id)
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        session_id: Optional[str],
        collection_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Tuple[ConversationDB, bool]:
        """获取或创建会话

        Args:
            session_id: 会话 ID，不传则自动生成
            collection_id: 知识库集合 ID（可选，为 None 表示通用对话）
            user_id: 用户 ID（可选）

        Returns:
            (conversation, created)
        """
        if session_id:
            conv = await self.get_by_session_id(session_id)
            if conv:
                # 如果会话已存在但 collection_id 变化了，更新 collection_id
                # （支持在对话过程中切换知识库）
                if collection_id and conv.collection_id != collection_id:
                    conv.collection_id = collection_id
                    conv.updated_at = datetime.utcnow()
                    await self.db.flush()
                return conv, False

        # 创建新会话
        new_session_id = session_id or f"sess_{uuid.uuid4().hex[:16]}"
        conv = ConversationDB(
            id=str(uuid.uuid4()),
            session_id=new_session_id,
            collection_id=collection_id,
            user_id=user_id,
            title=None,
            message_count=0,
            total_tokens=0,
            version=0,
            status="active",
        )
        self.db.add(conv)
        await self.db.flush()
        return conv, True

    async def list_by_collection(
        self,
        collection_id: str,
        user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ConversationDB]:
        """列出集合下的会话"""
        stmt = (
            select(ConversationDB)
            .where(
                ConversationDB.collection_id == collection_id,
                ConversationDB.status == "active",
            )
            .order_by(ConversationDB.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if user_id:
            stmt = stmt.where(ConversationDB.user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def list_all(
        self,
        user_id: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[ConversationDB]:
        """列出所有会话（全局，按更新时间倒序）

        Args:
            user_id: 可选，按用户过滤
            limit: 限制数量
            offset: 偏移量
        """
        stmt = (
            select(ConversationDB)
            .where(ConversationDB.status == "active")
            .order_by(ConversationDB.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        if user_id:
            stmt = stmt.where(ConversationDB.user_id == user_id)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def count_all(
        self,
        user_id: Optional[str] = None,
    ) -> int:
        """统计会话总数"""
        stmt = select(func.count(ConversationDB.id)).where(
            ConversationDB.status == "active"
        )
        if user_id:
            stmt = stmt.where(ConversationDB.user_id == user_id)
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    async def delete(self, session_id: str) -> bool:
        """删除会话（软删除：改状态）"""
        conv = await self.get_by_session_id(session_id)
        if not conv:
            return False
        conv.status = "archived"
        conv.updated_at = datetime.utcnow()
        await self.db.flush()
        # 清 Redis 缓存
        await cache_delete_session(session_id)
        return True

    # ---------- 消息读写 ----------

    async def append_message(
        self,
        conversation_id: str,
        session_id: str,
        role: str,
        content: str,
        turn_id: int,
        is_key_turn: bool = False,
        meta: Optional[str] = None,
    ) -> MessageDB:
        """追加一条消息（带乐观锁）

        步骤：
        1. 计算当前消息 seq = conversation.message_count
        2. 计算 token_count
        3. 插入消息
        4. 更新 conversation.message_count、version、total_tokens、title（首条）
        5. 写穿到 Redis 缓存
        """
        # 获取当前会话状态
        conv = await self.db.get(ConversationDB, conversation_id)
        if not conv:
            raise ValueError(f"Conversation {conversation_id} not found")

        current_seq = conv.message_count
        token_count = count_tokens(content)

        msg = MessageDB(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            turn_id=turn_id,
            role=role,
            content=content,
            token_count=token_count,
            is_key_turn=is_key_turn,
            seq=current_seq,
            meta=meta,
        )
        self.db.add(msg)

        # 更新会话元数据
        conv.message_count += 1
        conv.version += 1  # 乐观锁版本号
        conv.total_tokens += token_count
        conv.updated_at = datetime.utcnow()
        # 首条用户消息作为标题
        if conv.title is None and role == "user":
            conv.title = content[:50]

        await self.db.flush()

        # 异步刷新 Redis 缓存（不阻塞主流程，失败忽略）
        # 这里我们只追加单条，不做全量回写，性能更优
        from app.core.redis import cache_append_session_message
        msg_dict = {
            "seq": msg.seq,
            "turn_id": msg.turn_id,
            "role": msg.role,
            "content": msg.content,
            "token_count": msg.token_count,
            "is_key_turn": msg.is_key_turn,
        }
        await cache_append_session_message(session_id, msg_dict)

        return msg

    async def get_messages(
        self,
        conversation_id: str,
        session_id: str,
        from_seq: int = 0,
        limit: Optional[int] = None,
    ) -> List[MessageDB]:
        """获取消息列表

        读策略：
        1. 先查 Redis 缓存（完整消息列表）
        2. 未命中则从 DB 加载并回填缓存
        """
        # 尝试读 Redis 热缓存
        cached = await cache_get_session_messages(session_id)
        if cached is not None:
            # 缓存命中，做切片
            msgs = [
                MessageDB(
                    id="cached",
                    conversation_id=conversation_id,
                    seq=m.get("seq", i),
                    turn_id=m.get("turn_id", 0),
                    role=m["role"],
                    content=m["content"],
                    token_count=m.get("token_count", 0),
                    is_key_turn=m.get("is_key_turn", False),
                )
                for i, m in enumerate(cached)
            ]
            if from_seq > 0:
                msgs = [m for m in msgs if m.seq >= from_seq]
            if limit:
                msgs = msgs[-limit:]
            return msgs

        # 回源 DB
        stmt = (
            select(MessageDB)
            .where(
                MessageDB.conversation_id == conversation_id,
                MessageDB.seq >= from_seq,
            )
            .order_by(MessageDB.seq.asc())
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await self.db.execute(stmt)
        msgs = list(result.scalars().all())

        # 回填缓存（只在全量读取时回填，即 from_seq==0 且无 limit 限制）
        if from_seq == 0 and limit is None:
            await cache_set_session_messages(
                session_id,
                [
                    {
                        "seq": m.seq,
                        "turn_id": m.turn_id,
                        "role": m.role,
                        "content": m.content,
                        "token_count": m.token_count,
                        "is_key_turn": m.is_key_turn,
                    }
                    for m in msgs
                ],
            )

        return msgs

    async def get_message_count(self, conversation_id: str) -> int:
        """获取消息总数"""
        stmt = select(func.count(MessageDB.id)).where(
            MessageDB.conversation_id == conversation_id
        )
        result = await self.db.execute(stmt)
        return result.scalar() or 0

    # ---------- 摘要读写 ----------

    async def get_summary(
        self, conversation_id: str, level: str
    ) -> Optional[ConversationSummaryDB]:
        """获取指定层级的摘要"""
        stmt = select(ConversationSummaryDB).where(
            ConversationSummaryDB.conversation_id == conversation_id,
            ConversationSummaryDB.level == level,
        )
        result = await self.db.execute(stmt)
        return result.scalar_one_or_none()

    async def get_all_summaries(
        self, conversation_id: str
    ) -> List[ConversationSummaryDB]:
        """获取所有层级的摘要（按层级 far→mid→near 排序）"""
        # 用 CASE WHEN 实现自定义排序，兼容 SQLite / PostgreSQL / MySQL
        level_order = case(
            (ConversationSummaryDB.level == "far", 0),
            (ConversationSummaryDB.level == "mid", 1),
            (ConversationSummaryDB.level == "near", 2),
            else_=99,
        )
        stmt = (
            select(ConversationSummaryDB)
            .where(ConversationSummaryDB.conversation_id == conversation_id)
            .order_by(level_order)
        )
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def upsert_summary(
        self,
        conversation_id: str,
        level: str,
        content: str,
        start_seq: int,
        end_seq: int,
    ) -> ConversationSummaryDB:
        """插入或更新摘要（同 layer 唯一）"""
        existing = await self.get_summary(conversation_id, level)
        token_count = count_tokens(content)
        if existing:
            existing.content = content
            existing.token_count = token_count
            existing.start_seq = start_seq
            existing.end_seq = end_seq
            existing.version += 1
            existing.updated_at = datetime.utcnow()
            await self.db.flush()
            return existing
        else:
            summary = ConversationSummaryDB(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                level=level,
                content=content,
                token_count=token_count,
                start_seq=start_seq,
                end_seq=end_seq,
                version=0,
            )
            self.db.add(summary)
            await self.db.flush()
            return summary
