"""
通用响应 Schema
"""

from typing import Generic, TypeVar, Optional, Any
from pydantic import BaseModel, Field

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """统一响应格式"""
    code: int = Field(default=0, description="状态码，0 表示成功")
    message: str = Field(default="success", description="消息")
    data: Optional[T] = Field(default=None, description="数据")
    request_id: Optional[str] = Field(default=None, description="请求 ID")


class PaginationData(BaseModel, Generic[T]):
    """分页数据"""
    items: list[T]
    total: int
    page: int
    page_size: int


class PaginationParams(BaseModel):
    """分页参数"""
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=20, ge=1, le=100, description="每页数量")


def success(data: Any = None, message: str = "success") -> dict:
    """构造成功响应"""
    return {"code": 0, "message": message, "data": data}


def paginated(items: list, total: int, page: int, page_size: int) -> dict:
    """构造分页响应"""
    return success({
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    })
