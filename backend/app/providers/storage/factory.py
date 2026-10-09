"""
存储后端工厂
"""

from .base import BaseStorage
from .local_storage import LocalStorage
from app.core.config import settings
from app.core.exceptions import InternalError


_storage_instance: BaseStorage | None = None


def get_storage() -> BaseStorage:
    """获取存储后端单例"""
    global _storage_instance
    if _storage_instance is None:
        backend = settings.storage_backend.lower()
        if backend == "local":
            _storage_instance = LocalStorage(settings.storage_root)
        else:
            raise InternalError(f"Unsupported storage backend: {backend}")
    return _storage_instance
