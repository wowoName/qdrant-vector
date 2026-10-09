"""
Upload Session ORM 模型（分片上传）
"""

import uuid
from datetime import datetime

from sqlalchemy import JSON, BigInteger, Column, DateTime, Index, Integer, String

from app.core.database import Base


class UploadSessionDB(Base):
    """分片上传会话"""

    __tablename__ = "upload_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    file_name = Column(String(255), nullable=False)
    file_size = Column(BigInteger, nullable=False)
    total_chunks = Column(Integer, nullable=False)
    chunk_size = Column(Integer, nullable=False)
    file_hash = Column(String(64), nullable=True, index=True)
    directory_id = Column(String(36), nullable=True)
    collection_id = Column(String(36), nullable=False)
    uploaded_chunks = Column(JSON, default=[])  # 已上传的分片索引列表
    status = Column(String(20), default="uploading")  # uploading/completed/expired/failed
    temp_dir = Column(String(500), nullable=False)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("idx_status_expires", "status", "expires_at"),)
