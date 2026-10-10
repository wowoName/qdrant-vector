"""
租户磁盘配额工具（SaaS）

配额模型：
- 每个租户（tenant_admin）拥有独立的磁盘配额 storage_quota（字节，None 表示不限制）
- 租户用量 = 该租户自身 + 名下子用户所拥有知识库中所有文档的当前文件大小之和
- super_admin（平台方）不设配额（不限制）
- 普通用户（子用户）继承所属租户的配额，仅用于展示

数据归属：collections.owner_user_id → users.id，documents.collection_id → collections.id。
"""

import os
import shutil

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import StorageQuotaExceededError, ValidationError
from app.models.db.collection import CollectionDB
from app.models.db.document import DocumentDB
from app.models.db.user import UserDB

# 新租户默认配额（字节）
DEFAULT_TENANT_QUOTA_BYTES: int = settings.tenant_default_quota_mb * 1024 * 1024


def format_bytes(num: int) -> str:
    """将字节数格式化为易读字符串"""
    if not num or num < 0:
        return "0 B"
    if num < 1024:
        return f"{num} B"
    if num < 1024 * 1024:
        return f"{num / 1024:.1f} KB"
    if num < 1024 * 1024 * 1024:
        return f"{num / (1024 * 1024):.1f} MB"
    return f"{num / (1024 * 1024 * 1024):.2f} GB"


async def resolve_tenant(db: AsyncSession, user: UserDB) -> UserDB | None:
    """解析用户所属租户（配额承载者）

    - super_admin：返回 None（平台方，不限制）
    - tenant_admin：返回自身
    - user（子用户）：返回所属租户管理员；无有效归属时返回自身
    """
    if user.role == "super_admin":
        return None
    if user.role == "tenant_admin":
        return user
    if user.owner_admin_id:
        result = await db.execute(select(UserDB).where(UserDB.id == user.owner_admin_id))
        owner = result.scalar_one_or_none()
        if owner and owner.role == "tenant_admin":
            return owner
    return user


async def get_tenant_user_ids(db: AsyncSession, tenant_admin_id: str) -> list[str]:
    """租户自身 + 名下子用户 ID 列表"""
    result = await db.execute(
        select(UserDB.id).where(UserDB.owner_admin_id == tenant_admin_id)
    )
    return [tenant_admin_id, *[row[0] for row in result.all()]]


async def calc_used_bytes(db: AsyncSession, owner_user_ids: list[str]) -> int:
    """统计这些归属用户所拥有知识库中所有文档的当前文件大小之和"""
    if not owner_user_ids:
        return 0
    result = await db.execute(
        select(func.coalesce(func.sum(DocumentDB.file_size), 0))
        .select_from(DocumentDB)
        .join(CollectionDB, DocumentDB.collection_id == CollectionDB.id)
        .where(CollectionDB.owner_user_id.in_(owner_user_ids))
    )
    return int(result.scalar() or 0)


async def get_usage_map(db: AsyncSession, owner_user_ids: list[str]) -> dict[str, int]:
    """按知识库归属用户聚合已用空间：{owner_user_id: used_bytes}"""
    if not owner_user_ids:
        return {}
    result = await db.execute(
        select(
            CollectionDB.owner_user_id,
            func.coalesce(func.sum(DocumentDB.file_size), 0),
        )
        .select_from(DocumentDB)
        .join(CollectionDB, DocumentDB.collection_id == CollectionDB.id)
        .where(CollectionDB.owner_user_id.in_(owner_user_ids))
        .group_by(CollectionDB.owner_user_id)
    )
    return {row[0]: int(row[1] or 0) for row in result.all()}


async def get_storage_info(db: AsyncSession, user: UserDB) -> dict:
    """返回当前用户所属租户的存储信息

    {storage_quota: int|None, storage_used: int, storage_unlimited: bool}
    """
    tenant = await resolve_tenant(db, user)
    if tenant is None:
        # 平台方：不限制，仅统计自身用量用于展示
        used = await calc_used_bytes(db, [user.id])
        return {"storage_quota": None, "storage_used": used, "storage_unlimited": True}

    member_ids = await get_tenant_user_ids(db, tenant.id)
    used = await calc_used_bytes(db, member_ids)
    return {
        "storage_quota": tenant.storage_quota,
        "storage_used": used,
        "storage_unlimited": tenant.storage_quota is None,
    }


async def check_storage_quota(
    db: AsyncSession, collection_owner_id: str | None, add_bytes: int
) -> None:
    """上传前基于知识库归属用户的租户校验配额，超限抛 StorageQuotaExceededError

    - collection_owner_id：目标知识库的归属用户（决定计入哪个租户）
    - add_bytes：本次新增占用（替换已有文件时为增量，<=0 不校验）
    """
    if add_bytes <= 0 or not collection_owner_id:
        return

    result = await db.execute(select(UserDB).where(UserDB.id == collection_owner_id))
    owner = result.scalar_one_or_none()
    if owner is None:
        return

    tenant = await resolve_tenant(db, owner)
    if tenant is None or tenant.storage_quota is None:
        return  # 不限制

    member_ids = await get_tenant_user_ids(db, tenant.id)
    used = await calc_used_bytes(db, member_ids)
    if used + add_bytes > tenant.storage_quota:
        remain = max(0, tenant.storage_quota - used)
        raise StorageQuotaExceededError(
            message=(
                f"存储空间不足：租户配额 {format_bytes(tenant.storage_quota)}，"
                f"已使用 {format_bytes(used)}，剩余 {format_bytes(remain)}，"
                f"本次需 {format_bytes(add_bytes)}。请删除部分文件或联系管理员扩容。"
            )
        )


async def get_document_count_map(
    db: AsyncSession, owner_user_ids: list[str]
) -> dict[str, int]:
    """按知识库归属用户聚合文档数量：{owner_user_id: document_count}"""
    if not owner_user_ids:
        return {}
    result = await db.execute(
        select(CollectionDB.owner_user_id, func.count(DocumentDB.id))
        .select_from(DocumentDB)
        .join(CollectionDB, DocumentDB.collection_id == CollectionDB.id)
        .where(CollectionDB.owner_user_id.in_(owner_user_ids))
        .group_by(CollectionDB.owner_user_id)
    )
    return {row[0]: int(row[1] or 0) for row in result.all()}


def get_disk_usage(path: str) -> dict:
    """获取指定路径所在磁盘的容量信息（total/used/free 均为字节）

    路径不存在时向上回溯到最近的已存在目录，避免因存储目录尚未创建而报错。
    """
    probe = os.path.abspath(path)
    while probe and not os.path.exists(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            break
        probe = parent
    try:
        total, used, free = shutil.disk_usage(probe)
    except OSError:
        return {"path": path, "mount": probe, "total": 0, "used": 0, "free": 0, "percent": 0.0}
    percent = round(used / total * 100, 1) if total else 0.0
    return {"path": path, "mount": probe, "total": total, "used": used, "free": free, "percent": percent}


def get_allocatable_capacity(disk_total: int) -> int:
    """可分配配额上限 = 磁盘总容量 × 可分配比例（默认 70%）

    预留一部分磁盘空间给系统/日志/向量库等，避免配额吃满整块盘。
    """
    if disk_total <= 0:
        return 0
    return int(disk_total * settings.storage_allocatable_ratio)


async def get_tenants_usage(db: AsyncSession, owner_id: str | None = None) -> list[dict]:
    """获取各租户（含平台方）的磁盘用量明细

    租户 = role in (super_admin, tenant_admin)；用量含其名下子用户所拥有知识库的文档。
    owner_id 传入时仅返回该账号（用于租户管理员查看自身）。
    """
    owner_cond = [UserDB.role.in_(["super_admin", "tenant_admin"])]
    if owner_id is not None:
        owner_cond.append(UserDB.id == owner_id)

    owners = (
        await db.execute(select(UserDB).where(*owner_cond).order_by(UserDB.created_at.asc()))
    ).scalars().all()
    if not owners:
        return []

    owner_ids = [o.id for o in owners]
    children = (
        await db.execute(
            select(UserDB).where(UserDB.role == "user", UserDB.owner_admin_id.in_(owner_ids))
        )
    ).scalars().all()

    by_owner: dict[str, list[UserDB]] = {}
    for c in children:
        by_owner.setdefault(c.owner_admin_id, []).append(c)

    all_ids = owner_ids + [c.id for c in children]
    usage_map = await get_usage_map(db, all_ids)
    count_map = await get_document_count_map(db, all_ids)

    tenants: list[dict] = []
    for o in owners:
        members = [o.id, *[k.id for k in by_owner.get(o.id, [])]]
        used = sum(usage_map.get(i, 0) for i in members)
        docs = sum(count_map.get(i, 0) for i in members)
        unlimited = o.role == "super_admin" or o.storage_quota is None
        percent = (
            round(used / o.storage_quota * 100, 1)
            if (o.storage_quota and not unlimited)
            else None
        )
        tenants.append(
            {
                "id": o.id,
                "email": o.email,
                "nickname": o.nickname,
                "role": o.role,
                "status": o.status,
                "quota": o.storage_quota,
                "unlimited": unlimited,
                "used": used,
                "percent": percent,
                "user_count": len(by_owner.get(o.id, [])),
                "document_count": docs,
                "created_at": o.created_at.isoformat() if o.created_at else None,
                "expires_at": o.expires_at.isoformat() if o.expires_at else None,
            }
        )
    return tenants


async def get_allocated_quota_total(
    db: AsyncSession, exclude_user_id: str | None = None
) -> int:
    """所有租户已分配配额之和（不含"不限制"），可排除指定用户（编辑场景）"""
    conditions = [
        UserDB.storage_quota.isnot(None),
        UserDB.role.in_(["super_admin", "tenant_admin"]),
    ]
    if exclude_user_id:
        conditions.append(UserDB.id != exclude_user_id)
    result = await db.execute(
        select(func.coalesce(func.sum(UserDB.storage_quota), 0)).where(*conditions)
    )
    return int(result.scalar() or 0)


async def check_quota_allocation(
    db: AsyncSession, new_quota: int | None, exclude_user_id: str | None = None
) -> None:
    """分配配额时校验总量不超过"可分配上限"，避免超售

    - new_quota 为 None 表示"不限制"，直接放行
    - 磁盘容量探测失败（total<=0）时跳过校验，避免误拦截
    - 校验基准为可分配上限 = 磁盘总容量 × storage_allocatable_ratio（默认 70%）
    """
    if new_quota is None:
        return

    disk = get_disk_usage(settings.storage_root)
    capacity = get_allocatable_capacity(disk.get("total", 0))
    if capacity <= 0:
        return

    allocated = await get_allocated_quota_total(db, exclude_user_id=exclude_user_id)
    if allocated + new_quota > capacity:
        remain = max(0, capacity - allocated)
        raise ValidationError(
            message=(
                f"配额超出可分配上限：可分配上限 {format_bytes(capacity)}"
                f"（磁盘总容量 {format_bytes(disk.get('total', 0))} × {int(settings.storage_allocatable_ratio * 100)}%），"
                f"其他租户已分配 {format_bytes(allocated)}，剩余可分配 {format_bytes(remain)}，"
                f"本次分配 {format_bytes(new_quota)}。请减少配额或改为「不限制」。"
            )
        )


async def compute_allocatable_quota(
    db: AsyncSession, desired: int | None, exclude_user_id: str | None = None
) -> int | None:
    """按"可分配上限"的剩余量对期望配额进行封顶（用于注册等"不应因配额失败"的场景）

    - desired 为 None（不限制）时原样返回
    - 磁盘容量探测失败（total<=0）时原样返回，避免误判
    - 否则返回 min(desired, 可分配上限 - 已分配配额)，最小为 0（即无剩余可分配时分配 0）
    """
    if desired is None:
        return None
    disk = get_disk_usage(settings.storage_root)
    capacity = get_allocatable_capacity(disk.get("total", 0))
    if capacity <= 0:
        return desired
    allocated = await get_allocated_quota_total(db, exclude_user_id=exclude_user_id)
    remain = max(0, capacity - allocated)
    return min(desired, remain)


