"""
依赖注入
"""

from typing import Optional

from fastapi import Request, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.config import settings


def get_request_id(request: Request) -> str:
    """获取请求 ID"""
    return getattr(request.state, "request_id", "")


async def get_user_id(x_user_id: Optional[str] = Header(default=None)) -> Optional[str]:
    """从请求头获取用户标识

    前端通过 X-User-ID 头传递匿名用户 ID。
    如果没有传，则返回 None（向后兼容）。
    """
    return x_user_id


__all__ = ["get_db", "get_request_id", "get_user_id", "settings"]
