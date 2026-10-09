"""
Auth API - 认证接口（SaaS）

- POST /auth/send-code   发送邮箱验证码
- POST /auth/register    注册（邮箱 + 验证码 + 密码）
- POST /auth/login       登录（返回 JWT）
- GET  /auth/me          获取当前用户信息
- POST /auth/logout      退出（前端清除 token 即可，此处保留接口语义）
"""

import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.database import get_db
from app.core.email_service import generate_code, send_code_email, smtp_configured
from app.core.logging import get_logger
from app.core.security import create_access_token, hash_password, verify_password
from app.models.db.user import EmailCodeDB, UserDB
from app.models.schemas.common import success

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["Auth"])

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


# ---------- 请求/响应模型 ----------


class SendCodeRequest(BaseModel):
    email: str = Field(..., description="接收验证码的邮箱")


class RegisterRequest(BaseModel):
    email: str
    code: str = Field(..., min_length=4, max_length=10, description="邮箱验证码")
    password: str = Field(..., min_length=6, max_length=64)
    nickname: str | None = Field(None, max_length=100)


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateMeRequest(BaseModel):
    """用户修改自己的信息（昵称/密码），不含过期日期等管理字段

    extra="forbid"：显式拒绝 expires_at/role 等管理字段，避免静默忽略
    修改密码时须同时提供当前密码 old_password 做二次校验
    """
    model_config = ConfigDict(extra="forbid")

    nickname: str | None = Field(None, max_length=100)
    old_password: str | None = Field(None, description="当前密码（修改密码时必填）")
    password: str | None = Field(None, min_length=6, max_length=64, description="新密码（留空则不修改）")


class LoginResponse(BaseModel):
    token: str
    user: dict


# ---------- 工具 ----------


def _validate_email(email: str) -> str:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        from app.core.exceptions import ValidationError

        raise ValidationError(message="邮箱格式不正确")
    return email


async def _get_super_admin_id(db: AsyncSession) -> str | None:
    """获取总管理员 ID（最早注册的 super_admin），自注册租户归属该管理员"""
    result = await db.execute(
        select(UserDB.id)
        .where(UserDB.role == "super_admin")
        .order_by(UserDB.created_at.asc())
        .limit(1)
    )
    return result.scalar_one_or_none()


def _issue_token(user: UserDB) -> str:
    return create_access_token(user.id, user.role)


# ---------- 接口 ----------


@router.post("/send-code", summary="发送邮箱验证码")
async def send_code(
    req: SendCodeRequest,
    db: AsyncSession = Depends(get_db),
):
    email = _validate_email(req.email)

    # 已注册的邮箱不允许再发注册验证码
    exists = await db.execute(select(UserDB.id).where(UserDB.email == email))
    if exists.scalar_one_or_none():
        from app.core.exceptions import ConflictError

        raise ConflictError(message="该邮箱已注册")

    # 防刷：限制发送间隔
    resend_limit = datetime.utcnow() - timedelta(seconds=settings.email_code_resend_seconds)
    recent = await db.execute(
        select(func.count(EmailCodeDB.id)).where(
            EmailCodeDB.email == email,
            EmailCodeDB.purpose == "register",
            EmailCodeDB.created_at >= resend_limit,
        )
    )
    if (recent.scalar() or 0) > 0:
        from app.core.exceptions import BadRequestError

        raise BadRequestError(
            message=f"发送过于频繁，请 {settings.email_code_resend_seconds} 秒后重试"
        )

    code = generate_code()
    expire_at = datetime.utcnow() + timedelta(minutes=settings.email_code_expire_minutes)

    db.add(EmailCodeDB(email=email, code=code, purpose="register", expires_at=expire_at))
    await db.flush()

    sent = False
    try:
        sent = await send_code_email(email, code)
    except Exception as e:
        logger.error(f"Send code email to {email} failed: {e}")
        from app.core.exceptions import InternalError

        raise InternalError(message="验证码邮件发送失败，请稍后重试")

    # 未配置 SMTP：开发模式下验证码已写入日志，随接口返回方便测试
    dev_code = None
    if not sent and settings.app_debug:
        dev_code = code

    return success(
        {
            "sent": sent,
            "smtp_configured": smtp_configured(),
            "expire_minutes": settings.email_code_expire_minutes,
            # 仅开发模式且未配置 SMTP 时返回，生产环境为 None
            "dev_code": dev_code,
            "message": "验证码已发送，请查收邮箱"
            if sent
            else "SMTP 未配置：验证码已记录到服务端日志（开发模式）",
        },
        message="验证码已生成",
    )


@router.post("/register", summary="注册用户（需邮箱验证码）")
async def register(
    req: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.core.exceptions import BadRequestError, ConflictError

    email = _validate_email(req.email)

    # 邮箱占用校验
    exists = await db.execute(select(UserDB).where(UserDB.email == email))
    if exists.scalar_one_or_none():
        raise ConflictError(message="该邮箱已注册")

    # 验证码校验（取该邮箱最新一条未使用的注册验证码）
    result = await db.execute(
        select(EmailCodeDB)
        .where(
            EmailCodeDB.email == email,
            EmailCodeDB.purpose == "register",
            EmailCodeDB.used == False,  # noqa: E712
        )
        .order_by(EmailCodeDB.created_at.desc())
        .limit(1)
    )
    code_record = result.scalar_one_or_none()
    if not code_record:
        raise BadRequestError(message="请先获取邮箱验证码")
    if code_record.expires_at <= datetime.utcnow():
        raise BadRequestError(message="验证码已过期，请重新获取")
    if code_record.code != req.code.strip():
        code_record.failed_attempts += 1
        if code_record.failed_attempts >= 5:
            code_record.used = True
            await db.flush()
            raise BadRequestError(message="验证码错误次数过多，已失效，请重新获取")
        await db.flush()
        raise BadRequestError(message="验证码不正确")

    # 标记验证码已使用
    code_record.used = True

    # 第一个注册的用户自动成为总管理员(super_admin)，其余自注册用户为租户管理员(tenant_admin)
    any_user = await db.execute(select(func.count(UserDB.id)))
    is_first = (any_user.scalar() or 0) == 0
    owner_admin_id = None if is_first else await _get_super_admin_id(db)

    user = UserDB(
        email=email,
        password_hash=hash_password(req.password),
        nickname=(req.nickname or email.split("@")[0]).strip()[:100],
        role="super_admin" if is_first else "tenant_admin",
        owner_admin_id=owner_admin_id,
        status="active",
        is_verified=True,
    )
    db.add(user)
    await db.flush()

    logger.info(f"User registered: {email} (role={user.role}, first={is_first})")
    return success(
        {"token": _issue_token(user), "user": user.to_dict()},
        message="注册成功",
    )


@router.post("/login", summary="登录（返回 JWT）")
async def login(
    req: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    from app.core.exceptions import AuthError

    email = _validate_email(req.email)
    result = await db.execute(select(UserDB).where(UserDB.email == email))
    user = result.scalar_one_or_none()

    # 统一错误信息，避免暴露邮箱是否存在
    if not user or not verify_password(req.password, user.password_hash):
        raise AuthError(message="邮箱或密码错误")
    if user.status != "active":
        raise AuthError(message="账号已被禁用，请联系管理员")
    if user.is_expired:
        raise AuthError(message="账号已过期，请联系管理员续期")
    if not user.is_verified:
        raise AuthError(message="邮箱未验证，请重新注册或联系管理员")

    user.last_login_at = datetime.utcnow()
    await db.flush()

    return success({"token": _issue_token(user), "user": user.to_dict()})


@router.get("/me", summary="获取当前登录用户信息")
async def me(user: UserDB = Depends(get_current_user)):
    return success(user.to_dict())


@router.patch("/me", summary="修改当前用户信息（昵称/密码）")
async def update_me(
    req: UpdateMeRequest,
    user: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """用户修改自己的昵称/密码（不能修改过期日期等管理字段）

    修改密码时须校验当前密码（二次校验），当前密码错误则拒绝
    """
    from app.core.exceptions import BadRequestError

    # 修改密码：必须先校验当前密码
    if req.password:
        if not req.old_password:
            raise BadRequestError(message="请输入当前密码")
        if not verify_password(req.old_password, user.password_hash):
            raise BadRequestError(message="当前密码不正确")

    if req.nickname is not None:
        user.nickname = req.nickname.strip()[:100] or user.nickname
    if req.password:
        user.password_hash = hash_password(req.password)
    await db.flush()
    logger.info(f"User {user.email} updated own profile")
    return success(user.to_dict(), message="已更新")
