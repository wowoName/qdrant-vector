"""
用户 / 邮箱验证码 ORM 模型（SaaS 三级层级体系）

设计要点：
- 用户统一存主库（PostgreSQL），包含注册日期 / 过期日期 / 角色等 SaaS 管理字段
- 三级层级：
  - super_admin（总管理员）：首个注册者，owner_admin_id=None，可见所有租户和用户
  - tenant_admin（租户管理员）：自注册用户，owner_admin_id=super_admin.id，可管理自己的子用户
  - user（普通用户）：由 tenant_admin 创建，owner_admin_id=tenant_admin.id，只能看自己的数据
- 知识库可见范围：super_admin=全部，tenant_admin=自己+名下用户，user=仅自己
- 聊天记录：跟随用户个人（每个用户只看自己的会话）
- 邮箱验证码独立表，支持注册验证
"""

import uuid

from sqlalchemy import BigInteger, Boolean, Column, DateTime, Integer, Index, String

from app.core.database import Base
from app.utils.datetime_utils import now


class UserDB(Base):
    """用户表"""

    __tablename__ = "users"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    # 登录邮箱（唯一标识）
    email = Column(String(255), unique=True, nullable=False, index=True)
    # bcrypt 密码哈希
    password_hash = Column(String(128), nullable=False)
    # 昵称（展示用）
    nickname = Column(String(100), nullable=True)
    # 角色：super_admin（总管理员）/ tenant_admin（租户管理员）/ user（普通用户）
    role = Column(String(20), nullable=False, default="user", index=True)
    # 所属上级 ID：super_admin 为 None；tenant_admin 指向 super_admin.id；user 指向 tenant_admin.id
    owner_admin_id = Column(String(36), nullable=True, index=True)
    # 状态：active / disabled
    status = Column(String(20), nullable=False, default="active")
    # 邮箱是否已验证
    is_verified = Column(Boolean, nullable=False, default=False)
    # 注册日期
    created_at = Column(DateTime, default=now, nullable=False, index=True)
    # 注册来源 IP（用于限制同一 IP 只能注册一个租户）
    register_ip = Column(String(64), nullable=True, index=True)
    # 过期日期（None 表示永不过期），过期后禁止登录和使用
    expires_at = Column(DateTime, nullable=True, index=True)
    # 磁盘配额（字节）：仅租户（tenant_admin）承载，None 表示不限制
    #  - 租户实际用量 = 该租户自身及其名下子用户所拥有知识库的文档大小之和
    #  - super_admin（平台方）为 None（不限制）；普通子用户继承所属租户配额
    storage_quota = Column(BigInteger, nullable=True)
    # 最近登录时间
    last_login_at = Column(DateTime, nullable=True)

    __table_args__ = (Index("idx_users_owner_role", "owner_admin_id", "role"),)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        return self.expires_at <= now()

    def to_dict(self) -> dict:
        """转换为响应字典（不含密码哈希）"""
        return {
            "id": self.id,
            "email": self.email,
            "nickname": self.nickname,
            "role": self.role,
            "owner_admin_id": self.owner_admin_id,
            "status": self.status,
            "is_verified": self.is_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "storage_quota": self.storage_quota,
            "last_login_at": self.last_login_at.isoformat() if self.last_login_at else None,
        }


class EmailCodeDB(Base):
    """邮箱验证码"""

    __tablename__ = "email_codes"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String(255), nullable=False, index=True)
    code = Column(String(10), nullable=False)
    # 用途：register（注册验证）
    purpose = Column(String(20), nullable=False, default="register")
    expires_at = Column(DateTime, nullable=False)
    used = Column(Boolean, nullable=False, default=False)
    # 失败尝试次数（超过 5 次后验证码失效，防暴力破解）
    failed_attempts = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=now, nullable=False)

    __table_args__ = (Index("idx_email_codes_email_purpose", "email", "purpose", "created_at"),)
