"""
System API
"""

from fastapi import APIRouter, Query
from sqlalchemy import text

from app import __version__
from app.core.config import settings
from app.core.database import async_session
from app.models.schemas.common import success
from app.providers.llm.factory import test_llm_connection
from app.repositories.qdrant_repo import get_qdrant

router = APIRouter(tags=["System"])


@router.get("/health", summary="健康检查")
async def health_check():
    # 检查数据库连接
    db_status = {"status": "unknown"}
    try:
        async with async_session() as session:
            await session.execute(text("SELECT 1"))
        db_status = {"status": "healthy"}
    except Exception as e:
        db_status = {"status": "unhealthy", "error": str(e)[:200]}

    # 检查 Qdrant 状态（不阻塞主健康检查）
    qdrant_status = {"status": "unknown"}
    try:
        qdrant_repo = get_qdrant()
        qdrant_status = await qdrant_repo.health_check()
    except Exception as e:
        qdrant_status = {"status": "unhealthy", "error": str(e)[:200]}

    # 综合状态：任意关键服务不健康则 overall 为 unhealthy
    overall_healthy = db_status.get("status") == "healthy" and qdrant_status.get("status") == "healthy"

    return success(
        {
            "status": "healthy" if overall_healthy else "degraded",
            "version": __version__,
            "app": settings.app_name,
            "env": settings.app_env,
            "services": {
                "database": db_status,
                "qdrant": qdrant_status,
            },
        }
    )


@router.get("/config", summary="获取公开配置")
async def get_public_config():
    return success(
        {
            "app_name": settings.app_name,
            "version": __version__,
            "embedding_model": settings.embedding_model,
            "chunk_size": settings.chunk_size,
            "chunk_strategy": settings.chunk_strategy,
            "storage_backend": settings.storage_backend,
            "upload_max_file_size": settings.upload_max_file_size,
            "upload_chunk_size": settings.upload_chunk_size,
            "llm": {
                "provider": settings.llm_provider,
                "model": settings.llm_model,
                "temperature": settings.llm_temperature,
                "max_tokens": settings.llm_max_tokens,
                "chat": {
                    "provider": settings.chat_llm_provider or settings.llm_provider,
                    "model": settings.chat_llm_model or settings.llm_model,
                },
                "rewrite": {
                    "provider": settings.rewrite_llm_provider or settings.llm_provider,
                    "model": settings.rewrite_llm_model or settings.llm_model,
                },
                "summary": {
                    "provider": settings.summary_llm_provider or settings.llm_provider,
                    "model": settings.summary_llm_model or settings.llm_model,
                },
            },
        }
    )


@router.post("/llm/test", summary="测试 LLM 连接")
async def test_llm(
    provider: str | None = Query(default=None, description="LLM 提供商，不填则使用默认配置"),
    model: str | None = Query(default=None, description="模型名/endpoint，不填则使用默认配置"),
):
    """
    测试 LLM 配置是否正确、连接是否可用。

    会发送一条简短的测试消息，验证 API Key、模型名、网络连接等。
    """
    result = await test_llm_connection(provider=provider, model=model)
    return success(result)
