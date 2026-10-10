"""
Directory ORM 模型
"""

import uuid

from sqlalchemy import Column, DateTime, Index, Integer, String

from app.core.database import Base
from app.utils.datetime_utils import now


class DirectoryDB(Base):
    """目录树节点"""

    __tablename__ = "directories"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    collection_id = Column(String(36), nullable=False, index=True)
    parent_id = Column(String(36), nullable=True, index=True)  # 根目录为 NULL
    name = Column(String(100), nullable=False)
    path = Column(String(500), default="")  # 冗余路径，便于查询
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)
    updated_at = Column(DateTime, default=now, onupdate=now)

    __table_args__ = (Index("idx_collection_parent", "collection_id", "parent_id"),)
