"""
对话数据库连接与初始化（独立于主库）

设计：
- 对话数据（会话/消息/摘要）独立存储到 PostgreSQL
- 业务元数据（集合/文档/任务等）留在主库（PostgreSQL）
- 如果未启用 chat_db（chat_db_enabled=false），则复用主库连接，保持向后兼容
"""

from typing import Optional
from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from .config import settings
from .logging import get_logger

logger = get_logger(__name__)

# 对话模型的独立 Base
ChatBase = declarative_base()

# 是否使用独立对话库
_use_separate_chat_db = settings.chat_db_enabled and settings.chat_database_url is not None

if _use_separate_chat_db:
    # 独立对话库：使用 PostgreSQL 连接池
    _chat_engine_kwargs = {
        "echo": False,
        "echo_pool": False,
        "future": True,
        "pool_size": settings.chat_db_pool_size,
        "max_overflow": settings.chat_db_max_overflow,
        "pool_recycle": settings.chat_db_pool_recycle,
        "pool_pre_ping": True,
        "connect_args": {"server_settings": {"timezone": settings.app_timezone}},
    }
    chat_engine = create_async_engine(
        settings.chat_database_url,
        **_chat_engine_kwargs,
    )
    chat_async_session = async_sessionmaker(
        chat_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autoflush=False,
    )
    logger.info(
        f"Separate chat database enabled: {settings.chat_db_driver} "
        f"//{settings.chat_db_host}:{settings.chat_db_port}/{settings.chat_db_name}"
    )
else:
    # 未启用独立对话库：复用主库引擎和会话
    from .database import engine as _main_engine, async_session as _main_async_session

    chat_engine = _main_engine
    chat_async_session = _main_async_session
    logger.info("Chat database: using main database (no separate chat db configured)")


async def init_chat_db() -> None:
    """初始化对话数据库表"""
    # 导入对话模型，确保 ChatBase 能识别
    from app.models.db.chat import ConversationDB, MessageDB, ConversationSummaryDB  # noqa: F401

    if _use_separate_chat_db:
        async with chat_engine.begin() as conn:
            await conn.run_sync(ChatBase.metadata.create_all)
        logger.info("Chat database tables initialized (separate db).")
    else:
        # 复用主库：对话表也在主库中创建
        # 注意：对话模型继承自 ChatBase，不继承 Base，
        # 所以 init_db() 的 Base.metadata.create_all 不会创建对话表。
        # 我们需要在这里也创建一次。
        async with chat_engine.begin() as conn:
            await conn.run_sync(ChatBase.metadata.create_all)
        logger.info("Chat database tables initialized (main db).")

    # 运行数据库迁移（对话表结构变更）
    try:
        from app.core.migrations import run_migrations_async

        # 确定对话库的 URL
        if _use_separate_chat_db:
            chat_db_url = settings.chat_database_url or ""
        else:
            chat_db_url = settings.database_url

        applied = await run_migrations_async(chat_engine, chat_db_url)
        if applied:
            logger.info(f"Chat database migrations applied: {applied}")
        else:
            logger.info("No chat database migrations needed.")
    except Exception as e:
        logger.warning(f"Chat database migration check failed (non-critical): {e}")


async def get_chat_db() -> AsyncSession:
    """获取对话数据库会话（依赖注入用）"""
    async with chat_async_session() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        else:
            try:
                await session.commit()
            except PendingRollbackError:
                # 会话已被标记为"待回滚"：说明本次请求中有语句失败，
                # 但异常被上层捕获（如 SSE 生成器）未上抛，此时提交必然失败。
                # 直接回滚并告警，避免 PendingRollbackError 掩盖真正的失败原因。
                await session.rollback()
                logger.error(
                    "Chat DB session was in a failed transaction at teardown; rolled back "
                    "instead of committing. Look for the earlier error in this request."
                )
