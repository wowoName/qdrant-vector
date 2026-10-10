"""
Collection ORM 模型
"""

import uuid

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text

from app.core.database import Base
from app.utils.datetime_utils import now


class CollectionDB(Base):
    """知识库/集合"""

    __tablename__ = "collections"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String(100), nullable=False, unique=True, index=True)
    description = Column(Text, default="")
    # 归属用户 ID（SaaS 隔离：用户只能访问自己的知识库）
    owner_user_id = Column(String(36), nullable=True, index=True)
    embedding_model = Column(String(100), nullable=False, default="BAAI/bge-m3")
    vector_dim = Column(Integer, nullable=False, default=1024)
    distance_metric = Column(String(20), nullable=False, default="cosine")
    is_active = Column(Boolean, default=True)
    document_count = Column(Integer, default=0)
    chunk_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now, index=True)
    updated_at = Column(DateTime, default=now, onupdate=now)
