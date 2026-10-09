"""
数据库连接与初始化
"""

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

if "sqlite" in settings.db_driver:
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # PostgreSQL / MySQL 等服务端数据库：启用连接池
    _engine_kwargs["pool_size"] = settings.db_pool_size
    _engine_kwargs["max_overflow"] = settings.db_max_overflow
    _engine_kwargs["pool_recycle"] = settings.db_pool_recycle
    _engine_kwargs["pool_pre_ping"] = True  # 连接前先 ping，确保连接有效

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
            await session.commit()
        except Exception:
            await session.rollback()
            raise
