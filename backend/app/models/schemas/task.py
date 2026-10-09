"""
Task 相关 Schema
"""

from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field


class TaskResponse(BaseModel):
    id: str
    type: str
    status: str
    priority: int
    progress: float = 0.0
    progress_detail: Optional[dict] = None
    result: Optional[Any] = None
    error_message: Optional[str] = None
    retry_count: int = 0
    max_retries: int = 3
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
