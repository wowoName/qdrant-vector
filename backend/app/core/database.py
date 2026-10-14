"""
数据库连接与初始化（PostgreSQL）
"""

from sqlalchemy.exc import PendingRollbackError
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base

from .config import settings
from .logging import get_logger

logger = get_logger(__name__)

# SQLAlchemy 基类
Base = declarative_base()

# 构造引擎的通用参数
# echo=False: 不通过 SQLAlchemy 打印 SQL（改用应用层日志控制，避免刷屏）
# 如需调试 SQL，将 log_level 设为 DEBUG 并开启 sqlalchemy.engine 日志
_engine_kwargs = {
    "echo": False,
    "echo_pool": False,
    "future": True,
}

# PostgreSQL 连接池配置
_engine_kwargs["pool_size"] = settings.db_pool_size
_engine_kwargs["max_overflow"] = settings.db_max_overflow
_engine_kwargs["pool_recycle"] = settings.db_pool_recycle
_engine_kwargs["pool_pre_ping"] = True  # 连接前先 ping，确保连接有效

# 会话时区：让 PostgreSQL 会话按应用时区（默认北京时间）解释 now()/current_timestamp
_engine_kwargs["connect_args"] = {
    "command_timeout": settings.db_command_timeout,
    "server_settings": {"timezone": settings.app_timezone}
}

# 异步引擎
engine = create_async_engine(
    settings.database_url,
    **_engine_kwargs,
)

# 异步会话工厂
async_session = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def init_db() -> None:
    """初始化主数据库表（业务元数据：集合/文档/任务/上传/用户 等）"""
    # 导入所有主库模型，确保 Base 能识别
    from app.models.db import (  # noqa: F401
        collection, directory, document, document_record, task, upload, chat, user,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 运行主库迁移（如表结构新增列）
    try:
        from app.core.migrations import run_migrations_async

        applied = await run_migrations_async(engine, settings.database_url)
        if applied:
            logger.info(f"Main database migrations applied: {applied}")
    except Exception as e:
        logger.warning(f"Main database migration check failed (non-critical): {e}")

    logger.info("Main database initialized.")


async def get_db() -> AsyncSession:
    """获取数据库会话（依赖注入用）"""
    async with async_session() as session:
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
                    "DB session was in a failed transaction at teardown; rolled back "
                    "instead of committing. Look for the earlier error in this request."
                )
