"""
Collection 相关 Schema
"""

from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class CollectionBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100, description="集合名称")
    description: Optional[str] = Field(default="", description="描述")
    embedding_model: str = Field(default="BAAI/bge-m3", description="Embedding 模型")
    vector_dim: int = Field(default=1024, gt=0, description="向量维度")
    distance_metric: str = Field(default="cosine", description="距离度量")


class CollectionCreate(CollectionBase):
    """创建 Collection 请求"""
    pass


class CollectionUpdate(BaseModel):
    """更新 Collection 请求"""
    name: Optional[str] = Field(default=None, min_length=1, max_length=100)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CollectionResponse(CollectionBase):
    """Collection 响应"""
    id: str
    is_active: bool
    document_count: int = 0
    chunk_count: int = 0
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CollectionStats(BaseModel):
    """Collection 统计信息"""
    id: str
    name: str
    document_count: int
    chunk_count: int
    vector_count: int
    storage_size: str = "0 B"
