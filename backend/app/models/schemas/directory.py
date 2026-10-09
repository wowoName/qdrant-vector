"""
Directory 相关 Schema
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


class DirectoryCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    parent_id: Optional[str] = None
    sort_order: int = 0


class DirectoryUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    sort_order: Optional[int] = None


class DirectoryMove(BaseModel):
    parent_id: Optional[str] = None


class DirectoryResponse(BaseModel):
    id: str
    collection_id: str
    parent_id: Optional[str]
    name: str
    path: str
    sort_order: int
    created_at: datetime

    class Config:
        from_attributes = True


class DirectoryTreeNode(BaseModel):
    id: str
    name: str
    path: str
    parent_id: Optional[str]
    sort_order: int
    children: List["DirectoryTreeNode"] = []
    document_count: int = 0

    class Config:
        from_attributes = True


DirectoryTreeNode.model_rebuild()
