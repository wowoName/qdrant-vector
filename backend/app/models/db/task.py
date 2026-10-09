"""
Task ORM 模型
"""

import uuid
from datetime import datetime
from sqlalchemy import Column, String, Integer, DateTime, Text, JSON, Float

from app.core.database import Base


class TaskDB(Base):
    """异步任务"""

    __tablename__ = "tasks"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    type = Column(String(50), nullable=False, index=True)  # document_process/collection_rebuild/...
    status = Column(String(20), default="pending", index=True)
    # pending/queued/running/completed/failed/cancelled/paused
    priority = Column(Integer, default=5)
    payload = Column(JSON, default={})
    result = Column(JSON, nullable=True)
    error_message = Column(Text, nullable=True)
    progress = Column(Float, default=0.0)  # 0.0 - 1.0
    progress_detail = Column(JSON, nullable=True)
    retry_count = Column(Integer, default=0)
    max_retries = Column(Integer, default=3)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
