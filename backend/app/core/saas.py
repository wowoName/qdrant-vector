"""
SaaS 数据可见范围工具（三级层级体系）

层级模型：
- super_admin（总管理员）：可见所有用户的数据（所有租户 + 所有子用户）
- tenant_admin（租户管理员）：可见自己 + 名下子用户的数据
- user（普通用户）：可见自己 + 所属租户管理员（父级）的数据

数据归属：collections.owner_user_id、conversations.user_id 等。

可见范围规则（用于知识库等共享资源）：
- super_admin：所有用户 ID
- tenant_admin：[自己] + owner_admin_id == 自己的用户 ID
- user：[自己] + 所属租户管理员（父级）ID（实现租户内知识库共享）

聊天记录不使用此范围，始终按 user_id 隔离（每个用户只看自己的会话）。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.db.user import UserDB


async def get_visible_user_ids(db: AsyncSession, user: UserDB) -> list[str]:
    """获取当前用户可见的数据归属用户 ID 列表

    - super_admin：所有用户 ID（总管理员可见全部）
    - tenant_admin：[自己] + 名下子用户的 ID
    - user：[自己] + 所属租户管理员（父级）的 ID
    """
    if user.role == "super_admin":
        result = await db.execute(select(UserDB.id))
        return [row[0] for row in result.all()]

    if user.role == "tenant_admin":
        result = await db.execute(
            select(UserDB.id).where(UserDB.owner_admin_id == user.id)
        )
        managed = [row[0] for row in result.all()]
        return [user.id, *managed]

    # 普通用户：若归属租户管理员，则与所属租户共享知识库（可见父级租户的数据）
    if user.owner_admin_id:
        owner_role = await db.execute(
            select(UserDB.role).where(UserDB.id == user.owner_admin_id)
        )
        if owner_role.scalar_one_or_none() == "tenant_admin":
            return [user.id, user.owner_admin_id]
    return [user.id]
