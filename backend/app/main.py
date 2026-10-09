"""
Vector Data Manager - FastAPI 应用入口
"""

import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# 确保项目根目录在路径中
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import __version__
from app.api.v1 import api_router
from app.core.chat_database import init_chat_db
from app.core.config import settings
from app.core.database import async_session, init_db
from app.core.exceptions import AppException
from app.core.logging import get_logger, setup_logging
from app.core.middleware import RequestIDMiddleware
from app.providers.embedding.factory import get_embedding
from app.services.sync_service import run_startup_sync
from app.services.task_service import start_task_workers, stop_task_workers

# 初始化日志
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动
    logger.info(f"Starting {settings.app_name} v{__version__}...")
    logger.info(f"Environment: {settings.app_env}")

    # 初始化主数据库
    await init_db()
    logger.info("Main database initialized.")

    # 初始化对话数据库
    await init_chat_db()
    logger.info("Chat database initialized.")

    # 启动任务 Worker
    await start_task_workers(async_session)
    logger.info("Task workers started.")

    # 预加载 Embedding 模型（避免首次处理时的冷启动延迟）
    try:
        emb = await get_embedding()
        logger.info(
            f"Embedding model loaded: {emb.model_name} "
            f"(dim={emb.dimension}, device={settings.embedding_device})"
        )
    except Exception as e:
        logger.warning(f"Failed to preload embedding model: {e}")

    # 启动时 Qdrant 数据同步（数据库 → Qdrant）
    try:
        await run_startup_sync(async_session)
    except Exception as e:
        logger.warning(f"Startup Qdrant sync failed: {e}")

    logger.info(f"{settings.app_name} is ready!")

    yield

    # 关闭
    logger.info("Shutting down...")
    await stop_task_workers()
    logger.info("Task workers stopped.")
    logger.info("Shutdown complete.")


def create_app() -> FastAPI:
    """创建 FastAPI 应用"""
    app = FastAPI(
        title=settings.app_name,
        description="Production-grade vector database management tool",
        version=__version__,
        docs_url="/docs" if settings.app_debug else None,
        redoc_url="/redoc" if settings.app_debug else None,
        lifespan=lifespan,
    )

    # CORS 中间件
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 请求 ID 中间件
    app.add_middleware(RequestIDMiddleware)

    # 注册路由
    app.include_router(api_router)

    # 健康检查（根路径）
    @app.get("/health", tags=["Health"])
    async def root_health():
        return {"status": "healthy", "version": __version__}

    @app.get("/healthz", tags=["Health"])
    async def healthz():
        return {"status": "ok"}

    # ---------- 异常处理 ----------

    @app.exception_handler(AppException)
    async def app_exception_handler(request: Request, exc: AppException):
        """应用自定义异常"""
        request_id = getattr(request.state, "request_id", "")
        logger.warning(f"AppException: {exc.code} - {exc.message} (rid: {request_id})")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.code,
                "message": exc.message,
                "detail": exc.detail,
                "request_id": request_id,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        """参数校验异常"""
        request_id = getattr(request.state, "request_id", "")
        errors = exc.errors()
        logger.warning(f"Validation error: {errors} (rid: {request_id})")
        return JSONResponse(
            status_code=422,
            content={
                "code": 42200,
                "message": "Validation Error",
                "detail": errors,
                "request_id": request_id,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        """HTTP 异常"""
        request_id = getattr(request.state, "request_id", "")
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": exc.status_code * 100,
                "message": exc.detail,
                "request_id": request_id,
            },
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        """未捕获的异常"""
        request_id = getattr(request.state, "request_id", "")
        logger.error(f"Unhandled exception: {exc}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={
                "code": 50000,
                "message": "Internal Server Error",
                "request_id": request_id,
            },
        )

    return app


app = create_app()


def main():
    """命令行入口"""
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.app_host,
        port=settings.app_port,
        workers=settings.workers if not settings.app_debug else None,
        reload=settings.app_debug,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
