"""
自定义异常定义
"""

from typing import Any, Optional


class AppException(Exception):
    """应用基础异常"""

    def __init__(
        self,
        message: str = "Internal Server Error",
        code: int = 50000,
        status_code: int = 500,
        detail: Optional[Any] = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.detail = detail
        super().__init__(message)


# ---------- 4xx 客户端错误 ----------

class BadRequestError(AppException):
    """请求参数错误"""

    def __init__(self, message: str = "Bad Request", detail: Any = None):
        super().__init__(
            message=message,
            code=40000,
            status_code=400,
            detail=detail,
        )


class ValidationError(BadRequestError):
    """参数校验失败"""

    def __init__(self, message: str = "Validation Error", detail: Any = None):
        super().__init__(message=message, detail=detail)
        self.code = 40001


class NotFoundError(AppException):
    """资源不存在"""

    def __init__(self, message: str = "Not Found", detail: Any = None):
        super().__init__(
            message=message,
            code=40400,
            status_code=404,
            detail=detail,
        )


class ConflictError(AppException):
    """资源冲突"""

    def __init__(self, message: str = "Conflict", detail: Any = None):
        super().__init__(
            message=message,
            code=40900,
            status_code=409,
            detail=detail,
        )


class UnauthorizedError(AppException):
    """未授权"""

    def __init__(self, message: str = "Unauthorized", detail: Any = None):
        super().__init__(
            message=message,
            code=40100,
            status_code=401,
            detail=detail,
        )


# ---------- 5xx 服务端错误 ----------

class InternalError(AppException):
    """内部错误"""

    def __init__(self, message: str = "Internal Server Error", detail: Any = None):
        super().__init__(
            message=message,
            code=50000,
            status_code=500,
            detail=detail,
        )


# ---------- 业务错误 ----------

class CollectionNotFoundError(NotFoundError):
    """Collection 不存在"""

    def __init__(self, collection_id: str):
        super().__init__(message=f"Collection not found: {collection_id}")
        self.code = 40401


class DocumentNotFoundError(NotFoundError):
    """文档不存在"""

    def __init__(self, document_id: str):
        super().__init__(message=f"Document not found: {document_id}")
        self.code = 40402


class TaskNotFoundError(NotFoundError):
    """任务不存在"""

    def __init__(self, task_id: str):
        super().__init__(message=f"Task not found: {task_id}")
        self.code = 40403


class FileTooLargeError(BadRequestError):
    """文件过大"""

    def __init__(self, max_size: int):
        super().__init__(message=f"File too large. Maximum size: {max_size} bytes")
        self.code = 40002


class UnsupportedFileTypeError(BadRequestError):
    """不支持的文件类型"""

    def __init__(self, file_type: str):
        super().__init__(message=f"Unsupported file type: {file_type}")
        self.code = 40003


class QdrantError(InternalError):
    """Qdrant 操作错误"""

    def __init__(self, message: str = "Qdrant operation failed", detail: Any = None):
        super().__init__(message=message, detail=detail)
        self.code = 50001


class QdrantConnectionError(QdrantError):
    """Qdrant 连接失败（服务不可达）"""

    def __init__(self, message: str = "Qdrant service unreachable", detail: Any = None):
        super().__init__(message=message, detail=detail)
        self.code = 50011


class QdrantCollectionNotFoundError(QdrantError):
    """Qdrant Collection 不存在"""

    def __init__(self, message: str = "Qdrant collection not found", detail: Any = None):
        super().__init__(message=message, detail=detail)
        self.code = 50012


class EmbeddingError(InternalError):
    """Embedding 模型错误"""

    def __init__(self, message: str = "Embedding failed", detail: Any = None):
        super().__init__(message=message, detail=detail)
        self.code = 50002
