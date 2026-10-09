"""
Chat ORM 模型 - 会话 / 消息 / 摘要

设计要点：
- 会话（conversation）：按 thread_id（即 session_id）隔离，带版本号（乐观并发控制）
- 消息（message）：事件溯源式存储，每条消息带 turn 轮次、token 统计、是否关键消息
- 摘要（summary）：分层摘要，按层级（near/mid/far）存储，滚动更新
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)

from app.core.chat_database import ChatBase


class ConversationDB(ChatBase):
    """对话会话（线程）

    对应 Thread / Conversation Thread。
    用 version 做乐观并发控制（OCC），每次写入消息后 version + 1。
    """

    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # 对外暴露的会话 ID（兼容原 session_id 语义）
    session_id = Column(String(64), unique=True, nullable=False, index=True)
    # 所属知识库集合（可选，为 null 表示通用对话，不绑定知识库）
    collection_id = Column(String(36), nullable=True, index=True)
    # 可选用户标识（分区键），便于按用户维度检索
    user_id = Column(String(64), nullable=True, index=True)
    # 会话标题（首条消息自动生成）
    title = Column(String(255), nullable=True)
    # 消息总条数
    message_count = Column(Integer, default=0)
    # 累计 token 消耗（输入+输出）
    total_tokens = Column(Integer, default=0)
    # 乐观锁版本号
    version = Column(Integer, default=0, nullable=False)
    # 状态：active / archived
    status = Column(String(20), default="active")
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, index=True)

    __table_args__ = (Index("idx_collection_user", "collection_id", "user_id"),)


class MessageDB(ChatBase):
    """对话消息（事件溯源：每条消息不可变追加）"""

    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False, index=True)
    # 轮次：同一 turn_id 表示同一轮交互（user + assistant + tool 算一轮）
    turn_id = Column(Integer, nullable=False, default=0)
    # 角色：user / assistant / system / tool
    role = Column(String(20), nullable=False, index=True)
    # 消息内容
    content = Column(Text, nullable=False)
    # 内容 token 数
    token_count = Column(Integer, default=0)
    # 工具调用相关（可选，JSON 字符串）
    tool_name = Column(String(100), nullable=True)
    tool_result = Column(Text, nullable=True)
    # 是否为关键消息（关键轮次锚定：不被摘要吞掉）
    is_key_turn = Column(Boolean, default=False, nullable=False)
    # 排序序号（在会话内递增，便于按序取出）
    seq = Column(Integer, nullable=False, default=0)
    # 元数据 JSON（可选，存模型名、延迟等）
    meta = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("idx_conv_seq", "conversation_id", "seq", unique=True),
        Index("idx_conv_turn", "conversation_id", "turn_id"),
    )


class ConversationSummaryDB(ChatBase):
    """会话摘要（分层摘要）

    level:
      - "far"  远期：覆盖更早的历史，粒度最粗（一段话）
      - "mid"  中期：覆盖中段历史，粒度中等（几句话）
      - "near" 近期滚动摘要：覆盖被挤出窗口但不远的部分（一段）
    """

    __tablename__ = "conversation_summaries"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    conversation_id = Column(String(36), ForeignKey("conversations.id"), nullable=False, index=True)
    # 摘要层级：far / mid / near
    level = Column(String(20), nullable=False, index=True)
    # 摘要内容
    content = Column(Text, nullable=False)
    # 摘要 token 数
    token_count = Column(Integer, default=0)
    # 覆盖的消息 seq 范围 [start_seq, end_seq]
    start_seq = Column(Integer, default=0)
    end_seq = Column(Integer, default=0)
    # 版本号（同层摘要更新时递增）
    version = Column(Integer, default=0, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("idx_conv_level", "conversation_id", "level", unique=True),)
