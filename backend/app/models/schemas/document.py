"""
Document 相关 Schema
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# ---------- Document ----------

class DocumentBase(BaseModel):
    name: str = Field(..., max_length=255, description="文档名称")
    file_type: str = Field(..., max_length=20, description="文件类型")
    file_size: int = Field(default=0, description="文件大小（字节）")
    directory_id: Optional[str] = None


class DocumentResponse(DocumentBase):
    id: str
    collection_id: str
    current_version_id: Optional[str] = None
    status: str
    error_message: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


# ---------- DocumentVersion ----------

class DocumentVersionResponse(BaseModel):
    id: str
    document_id: str
    version_num: int
    file_hash: Optional[str] = None
    chunk_count: int = 0
    status: str
    error_message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Chunk ----------

class ChunkResponse(BaseModel):
    id: str
    version_id: str
    document_id: str
    chunk_index: int
    text: str
    content_hash: Optional[str] = None
    page_num: Optional[int] = None
    char_start: int = 0
    char_end: int = 0
    token_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


# ---------- Upload ----------

class UploadInitRequest(BaseModel):
    file_name: str
    file_size: int
    total_chunks: int
    chunk_size: int
    file_hash: Optional[str] = None
    collection_id: str
    directory_id: Optional[str] = None


class UploadInitResponse(BaseModel):
    upload_id: str
    chunk_size: int
    expires_at: datetime
    status: str = "uploading"


class UploadStatusResponse(BaseModel):
    upload_id: str
    file_name: str
    total_chunks: int
    uploaded_count: int
    uploaded_chunks: List[int]
    status: str
    expires_at: datetime


class UploadCompleteRequest(BaseModel):
    file_hash: Optional[str] = None


class UploadCompleteResponse(BaseModel):
    document_id: str
    version_id: str
    status: str
    task_id: Optional[str] = None
    instant: bool = False  # 是否秒传


class SimpleUploadResponse(BaseModel):
    """普通单文件上传响应"""
    document_id: str
    version_id: str
    status: str
    task_id: Optional[str] = None
