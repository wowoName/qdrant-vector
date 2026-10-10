"""
Admin API - 管理员用户管理（SaaS 三级层级）

层级：
- super_admin（总管理员）：可见所有租户(tenant_admin)和所有普通用户(user)，可管理所有人
- tenant_admin（租户管理员）：可见和管理自己名下的子用户(user)
- user（普通用户）：无管理权限

- GET    /admin/users               用户列表（支持关键字搜索、分页、角色筛选）
- POST   /admin/users               创建子用户（无需邮箱验证，管理员直接开通）
- PATCH  /admin/users/{user_id}     更新用户（过期日期/状态/昵称/密码）
- DELETE /admin/users/{user_id}     删除用户
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, require_admin
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AuthError, ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.security import hash_password
from app.core.storage_quota import (
    check_quota_allocation,
    get_allocatable_capacity,
    get_disk_usage,
    get_tenants_usage,
    get_usage_map,
)
from app.models.db.user import UserDB
from app.models.schemas.common import paginated, success

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["Admin"])


# ---------- 请求模型 ----------


class AdminCreateUserRequest(BaseModel):
    email: str
    password: str = Field(..., min_length=6, max_length=64)
    nickname: str | None = Field(None, max_length=100)
    expires_at: datetime | None = Field(None, description="过期日期，空表示永不过期")


class AdminUpdateUserRequest(BaseModel):
    """管理员更新他人：只能设置过期日期、状态与存储配额（名称/密码由用户自行修改）

    extra="forbid"：显式拒绝 nickname/password/role 等越权字段，避免静默忽略
    """
    model_config = ConfigDict(extra="forbid")

    expires_at: datetime | None = Field(None, description="过期日期，空表示永不过期")
    status: str | None = Field(None, pattern="^(active|disabled)$")
    storage_quota: int | None = Field(
        None, ge=0, description="存储配额（字节），仅总管理员可设置，null 表示不限制"
    )


# ---------- 工具 ----------


async def _get_managed_user(db: AsyncSession, admin: UserDB, user_id: str) -> UserDB:
    """获取管理员可管理的用户

    - super_admin：可管理任何用户（但不能管理自己）
    - tenant_admin：只能管理 owner_admin_id == 自己 的子用户
    """
    result = await db.execute(select(UserDB).where(UserDB.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise NotFoundError(message="用户不存在")
    if user.id == admin.id:
        raise NotFoundError(message="不能管理自己的账号")

    if admin.role == "super_admin":
        # 总管理员可管理所有人，但不能管理自己（上方已拦截）和其他总管理员
        if user.role == "super_admin":
            raise NotFoundError(message="不能管理其他总管理员")
        return user
    elif admin.role == "tenant_admin":
        # 租户管理员只能管理名下子用户
        if user.owner_admin_id != admin.id:
            raise NotFoundError(message="用户不存在或不属于您的管理范围")
        return user
    else:
        raise NotFoundError(message="无管理权限")


# ---------- 接口 ----------


@router.get("/users", summary="用户列表（管理员）")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str | None = Query(None, description="按邮箱/昵称搜索"),
    status: str | None = Query(None, pattern="^(active|disabled)$"),
    role: str | None = Query(
        None,
        pattern="^(super_admin|tenant_admin|user)$",
        description="按角色筛选（仅 super_admin 可用）",
    ),
    admin: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_admin(admin)

    conditions: list = []
    if admin.role == "super_admin":
        # 总管理员：可见所有用户，不排除自己（方便查看全局）
        if role:
            conditions.append(UserDB.role == role)
    elif admin.role == "tenant_admin":
        # 租户管理员：只能看到自己的子用户（role=user 且 owner_admin_id=自己）
        conditions.append(UserDB.owner_admin_id == admin.id)
        conditions.append(UserDB.role == "user")

    if keyword:
        kw = f"%{keyword.strip()}%"
        conditions.append(or_(UserDB.email.ilike(kw), UserDB.nickname.ilike(kw)))
    if status:
        conditions.append(UserDB.status == status)

    total_result = await db.execute(select(func.count(UserDB.id)).where(*conditions))
    total = total_result.scalar() or 0

    result = await db.execute(
        select(UserDB)
        .where(*conditions)
        .order_by(UserDB.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    users = result.scalars().all()

    return paginated(
        items=[u.to_dict() for u in users],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/user-tree", summary="用户树（租户 → 子用户）")
async def user_tree(
    keyword: str | None = Query(None, description="按邮箱/昵称搜索用户"),
    tenant: str | None = Query(None, description="按租户名称（管理员邮箱/昵称）搜索"),
    status: str | None = Query(None, pattern="^(active|disabled)$"),
    created_from: datetime | None = Query(None, description="注册日期起（含）"),
    created_to: datetime | None = Query(None, description="注册日期止（含）"),
    expires_from: datetime | None = Query(None, description="到期日期起（含），永不过期不纳入"),
    expires_to: datetime | None = Query(None, description="到期日期止（含），永不过期不纳入"),
    admin: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """返回树形用户结构：根节点为租户（管理员），children 为其名下子用户

    - super_admin：所有管理员作为根节点（含自身），各自挂其子用户
    - tenant_admin：自身作为根节点，挂其子用户
    - tenant：只保留匹配的租户根节点
    - keyword/status/created_*/expires_*：过滤用户；根节点在其自身或任一子用户命中时保留
    """
    require_admin(admin)

    # 1) 根节点：可管理的"租户/管理员"
    if admin.role == "super_admin":
        owner_cond = [UserDB.role.in_(["super_admin", "tenant_admin"])]
    else:
        owner_cond = [UserDB.id == admin.id]
    owners = (
        await db.execute(
            select(UserDB).where(*owner_cond).order_by(UserDB.created_at.asc())
        )
    ).scalars().all()

    # 租户名称过滤（仅作用于根节点）
    if tenant:
        t = tenant.strip().lower()
        owners = [
            o
            for o in owners
            if t in (o.email or "").lower() or t in (o.nickname or "").lower()
        ]
    owner_ids = [o.id for o in owners]

    # 2) 子节点：这些管理员名下的普通用户
    children: list[UserDB] = []
    if owner_ids:
        children = (
            await db.execute(
                select(UserDB)
                .where(UserDB.role == "user", UserDB.owner_admin_id.in_(owner_ids))
                .order_by(UserDB.created_at.asc())
            )
        ).scalars().all()

    by_owner: dict[str, list[UserDB]] = {}
    for c in children:
        by_owner.setdefault(c.owner_admin_id, []).append(c)

    # 3) 存储用量：按租户（含名下子用户）汇总已用空间
    all_ids = [o.id for o in owners] + [c.id for c in children]
    usage_map = await get_usage_map(db, all_ids)

    def _storage_of(owner: UserDB) -> dict:
        member_ids = [owner.id, *[k.id for k in by_owner.get(owner.id, [])]]
        used = sum(usage_map.get(i, 0) for i in member_ids)
        unlimited = owner.role == "super_admin" or owner.storage_quota is None
        return {
            "storage_quota": owner.storage_quota,
            "storage_used": used,
            "storage_unlimited": unlimited,
        }

    # 4) 过滤（命中子用户则保留其父节点）
    kw = keyword.strip().lower() if keyword else None
    filtering = bool(
        kw or status or created_from or created_to or expires_from or expires_to
    )

    def _match(u: UserDB) -> bool:
        if status and u.status != status:
            return False
        if kw and kw not in (u.email or "").lower() and kw not in (u.nickname or "").lower():
            return False
        if created_from and (not u.created_at or u.created_at < created_from):
            return False
        if created_to and (not u.created_at or u.created_at > created_to):
            return False
        # 到期日期：永不过期（None）不纳入区间筛选
        if expires_from and (not u.expires_at or u.expires_at < expires_from):
            return False
        if expires_to and (not u.expires_at or u.expires_at > expires_to):
            return False
        return True

    def _node(u: UserDB, kids: list[UserDB]) -> dict:
        data = u.to_dict()
        data.update(_storage_of(u))
        data["children"] = [{**k.to_dict(), "children": []} for k in kids]
        return data

    items: list[dict] = []
    for o in owners:
        all_kids = by_owner.get(o.id, [])
        kids = [k for k in all_kids if _match(k)] if filtering else all_kids
        if not filtering or _match(o) or kids:
            items.append(_node(o, kids))

    return success(items)


@router.post("/users", summary="创建子用户（管理员开通，无需邮箱验证）")
async def create_user(
    req: AdminCreateUserRequest,
    admin: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_admin(admin)

    # 过期日期只有总管理员可设置（租户创建子用户时不允许）
    if req.expires_at is not None and admin.role != "super_admin":
        raise AuthError(message="只有总管理员可以设置过期日期", status_code=403)

    email = (req.email or "").strip().lower()
    if not email:
        raise ValidationError(message="邮箱不能为空")

    exists = await db.execute(select(UserDB.id).where(UserDB.email == email))
    if exists.scalar_one_or_none():
        raise ConflictError(message="该邮箱已注册")

    user = UserDB(
        email=email,
        password_hash=hash_password(req.password),
        nickname=(req.nickname or email.split("@")[0]).strip()[:100],
        role="user",
        owner_admin_id=admin.id,
        status="active",
        is_verified=True,
        expires_at=req.expires_at,
    )
    db.add(user)
    await db.flush()
    logger.info(f"Admin {admin.email} ({admin.role}) created user {email} (expires_at={req.expires_at})")
    return success(user.to_dict(), message="用户创建成功")


@router.patch("/users/{user_id}", summary="更新用户（过期日期/状态/存储配额）")
async def update_user(
    user_id: str,
    req: AdminUpdateUserRequest,
    admin: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """管理员更新他人：只能设置过期日期、状态与存储配额（名称/密码由用户自行在「个人信息」修改）"""
    require_admin(admin)

    # 过期日期只有总管理员可设置（租户只能改状态）
    if "expires_at" in req.model_fields_set and admin.role != "super_admin":
        raise AuthError(message="只有总管理员可以设置过期日期", status_code=403)
    # 存储配额只有总管理员可分配
    if "storage_quota" in req.model_fields_set and admin.role != "super_admin":
        raise AuthError(message="只有总管理员可以分配存储空间", status_code=403)

    user = await _get_managed_user(db, admin, user_id)

    if "storage_quota" in req.model_fields_set and user.role != "tenant_admin":
        raise ValidationError(message="存储空间仅可分配给租户管理员")

    # 校验分配后总配额不超过物理磁盘容量（避免超售）
    if "storage_quota" in req.model_fields_set:
        await check_quota_allocation(db, req.storage_quota, exclude_user_id=user.id)

    # 使用 model_fields_set 区分"未提供该字段"（跳过）和"显式传 null"（清除）
    if "expires_at" in req.model_fields_set:
        user.expires_at = req.expires_at
    if req.status is not None:
        user.status = req.status
    if "storage_quota" in req.model_fields_set:
        user.storage_quota = req.storage_quota

    await db.flush()
    logger.info(f"Admin {admin.email} ({admin.role}) updated user {user.email}")
    return success(user.to_dict(), message="用户已更新")


@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: str,
    admin: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    require_admin(admin)
    user = await _get_managed_user(db, admin, user_id)

    email = user.email
    await db.delete(user)
    logger.info(f"Admin {admin.email} ({admin.role}) deleted user {email}")
    return success({"deleted": True}, message="用户已删除")


@router.get("/disk", summary="磁盘管理（总容量与各租户用量）")
async def disk_overview(
    admin: UserDB = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """磁盘管理总览

    - disk：存储目录所在物理磁盘的容量（总量/已用/可用）
    - summary：租户数、配额总量、实际用量、文档数
    - tenants：各租户（含平台方）用量明细
      - super_admin：查看全部租户
      - tenant_admin：仅查看自身
    """
    require_admin(admin)

    owner_id = admin.id if admin.role == "tenant_admin" else None
    tenants = await get_tenants_usage(db, owner_id=owner_id)
    disk = get_disk_usage(settings.storage_root)

    # 配额统计仅纳入有限额租户（不限制的租户不计入配额总量，但其用量仍计入实际用量）
    limited = [t for t in tenants if not t["unlimited"]]
    total_quota = sum(t["quota"] or 0 for t in limited)
    limited_used = sum(t["used"] for t in limited)
    disk_total = disk.get("total", 0)
    # 可分配配额上限 = 磁盘总容量 × 可分配比例（默认 70%）
    allocatable_capacity = get_allocatable_capacity(disk_total)
    summary = {
        "tenant_count": len(tenants),
        "unlimited_count": len(tenants) - len(limited),
        "total_quota": total_quota,
        "total_used": sum(t["used"] for t in tenants),
        "total_documents": sum(t["document_count"] for t in tenants),
        "quota_used_percent": round(limited_used / total_quota * 100, 1) if total_quota else 0.0,
        # 可分配配额上限（磁盘总容量 × 比例）与剩余可分配
        "allocatable_capacity": allocatable_capacity,
        "allocatable_ratio": settings.storage_allocatable_ratio,
        "allocatable_remaining": max(0, allocatable_capacity - total_quota) if allocatable_capacity else 0,
        # 是否超售：已分配配额超过可分配上限
        "overcommit": bool(allocatable_capacity) and total_quota > allocatable_capacity,
    }

    return success(
        {
            "disk": disk,
            "summary": summary,
            "tenants": tenants,
        }
    )
