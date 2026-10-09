"""
Search / Vector 相关 Schema
"""

from typing import Optional, List, Any
from pydantic import BaseModel, Field


class SearchRequest(BaseModel):
    """语义检索请求"""
    query: str = Field(..., description="查询文本")
    top_k: int = Field(default=10, ge=1, le=100, description="返回数量")
    score_threshold: float = Field(default=0.0, ge=0.0, le=1.0, description="相似度阈值")
    directory_id: Optional[str] = Field(default=None, description="目录过滤")
    document_id: Optional[str] = Field(default=None, description="文档过滤")
    file_type: Optional[str] = Field(default=None, description="文件类型过滤")


class SearchResult(BaseModel):
    """检索结果"""
    chunk_id: str
    collection_id: str = ""
    document_id: str
    document_name: str
    version_id: str
    chunk_index: int
    text: str
    score: float
    page_num: Optional[int] = None
    file_type: Optional[str] = None
    payload: Optional[dict] = None


class SearchResponse(BaseModel):
    """检索响应"""
    results: List[SearchResult]
    total: int
    query: str
    latency_ms: float
