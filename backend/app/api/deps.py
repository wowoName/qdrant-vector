"""
依赖注入
"""

from typing import Optional

from fastapi import Depends, Request, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_db
from app.core.config import settings
from app.core.exceptions import AuthError
from app.core.security import decode_access_token
from app.models.db.user import UserDB


def get_request_id(request: Request) -> str:
    """获取请求 ID"""
    return getattr(request.state, "request_id", "")


async def _load_user(db: AsyncSession, user_id: str) -> UserDB:
    """加载用户并校验状态"""
    result = await db.execute(select(UserDB).where(UserDB.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise AuthError(message="用户不存在或已被删除")
    if user.status != "active":
        raise AuthError(message="账号已被禁用，请联系管理员")
    if user.is_expired:
        raise AuthError(message="账号已过期，请联系管理员续期")
    return user


async def get_optional_user(
    authorization: Optional[str] = Header(default=None),
    token: Optional[str] = Query(
        default=None,
        description="JWT，用于无法携带 Authorization 头的下载/预览链接（新窗口打开）",
    ),
    db: AsyncSession = Depends(get_db),
) -> Optional[UserDB]:
    """从 Authorization: Bearer <token> 或 ?token=<jwt> 获取当前用户（均未提供则返回 None）

    优先使用请求头；浏览器直接导航（新窗口/下载）无法附加请求头，此时可用 ?token= 兜底。
    """
    raw: Optional[str] = None
    if authorization and authorization.lower().startswith("bearer "):
        raw = authorization[7:].strip()
    elif token:
        raw = token.strip()

    if not raw:
        return None
    payload = decode_access_token(raw)
    if not payload:
        return None
    user_id = payload.get("uid") or payload.get("sub")
    if not user_id:
        return None
    return await _load_user(db, user_id)


async def get_current_user(
    user: Optional[UserDB] = Depends(get_optional_user),
) -> UserDB:
    """获取当前登录用户（未登录则 401）"""
    if not user:
        raise AuthError(message="未登录或登录已过期")
    return user


def require_admin(user: UserDB) -> UserDB:
    """校验当前用户是管理员（super_admin 或 tenant_admin），否则 403"""
    if user.role not in ("super_admin", "tenant_admin"):
        raise AuthError(message="无权限：需要管理员身份", status_code=403)
    return user


async def require_writer(user: UserDB = Depends(get_current_user)) -> UserDB:
    """校验当前用户有写权限（自用号 role=user 对知识库/文档只读）

    可见范围（读）与可管理范围（写）分离：
    - super_admin / tenant_admin：可写（范围由 get_visible_ids 决定）
    - user（自用号）：只读，禁止增删改知识库与文档
    """
    if user.role == "user":
        raise AuthError(
            message="无权限：自用号仅可查看和使用，不能修改知识库或文档",
            status_code=403,
        )
    return user


async def get_visible_ids(
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[str]:
    """当前用户可见的数据归属用户 ID 列表（admin 含名下用户），依赖注入用"""
    from app.core.saas import get_visible_user_ids

    return await get_visible_user_ids(db, user)


__all__ = [
    "get_db",
    "get_request_id",
    "get_current_user",
    "get_optional_user",
    "get_visible_ids",
    "require_admin",
    "require_writer",
    "settings",
]
