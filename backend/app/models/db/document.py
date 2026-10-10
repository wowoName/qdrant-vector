"""
Document & DocumentVersion & Chunk ORM 模型
"""

import uuid

from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)

from app.core.database import Base
from app.utils.datetime_utils import now


class DocumentDB(Base):
    """文档（文件级）"""

    __tablename__ = "documents"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    collection_id = Column(String(36), nullable=False, index=True)
    directory_id = Column(String(36), nullable=True, index=True)
    name = Column(String(255), nullable=False)
    file_type = Column(String(20), nullable=False)  # pdf/docx/txt/md/xlsx
    file_size = Column(BigInteger, default=0)
    current_version_id = Column(String(36), nullable=True)
    status = Column(String(20), default="pending")  # pending/processing/completed/failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now, index=True)
    updated_at = Column(DateTime, default=now, onupdate=now)

    __table_args__ = (Index("idx_collection_directory", "collection_id", "directory_id"),)


class DocumentVersionDB(Base):
    """文档版本"""

    __tablename__ = "document_versions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    document_id = Column(String(36), nullable=False, index=True)
    version_num = Column(Integer, nullable=False, default=1)
    file_hash = Column(String(64), nullable=True, index=True)  # SHA256
    chunk_count = Column(Integer, default=0)
    storage_path = Column(String(500), nullable=False)
    status = Column(String(20), default="processing")  # processing/completed/failed
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=now)

    __table_args__ = (Index("idx_doc_version", "document_id", "version_num"),)


class ChunkDB(Base):
    """文本块"""

    __tablename__ = "chunks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    version_id = Column(String(36), nullable=False, index=True)
    document_id = Column(String(36), nullable=False, index=True)
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=True, index=True)
    page_num = Column(Integer, nullable=True)
    char_start = Column(Integer, default=0)
    char_end = Column(Integer, default=0)
    token_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=now)

    __table_args__ = (Index("idx_version_index", "version_id", "chunk_index", unique=True),)
