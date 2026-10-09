"""
存储抽象基类
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, Optional


class BaseStorage(ABC):
    """存储后端抽象接口"""

    @abstractmethod
    async def upload(self, key: str, data: bytes) -> None:
        """上传文件（全量字节）"""
        pass

    @abstractmethod
    async def download(self, key: str) -> bytes:
        """下载文件（全量字节）"""
        pass

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """文件是否存在"""
        pass

    @abstractmethod
    async def delete(self, key: str) -> None:
        """删除文件"""
        pass

    @abstractmethod
    async def get_size(self, key: str) -> int:
        """获取文件大小（字节）"""
        pass

    @abstractmethod
    def get_local_path(self, key: str) -> Optional[str]:
        """获取本地文件路径（如果存储后端支持本地访问）"""
        pass
