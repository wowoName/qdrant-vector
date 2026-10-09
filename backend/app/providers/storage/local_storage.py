"""
本地文件系统存储
"""

import os
from pathlib import Path
from typing import Optional

import aiofiles

from .base import BaseStorage
from app.core.logging import get_logger

logger = get_logger(__name__)


class LocalStorage(BaseStorage):
    """本地文件系统存储后端"""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir).resolve()
        self.root_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage initialized at: {self.root_dir}")

    def _resolve_path(self, key: str) -> Path:
        """解析 key 为本地路径，防止路径遍历"""
        # 规范化 key，移除前导斜杠
        key = key.lstrip("/").lstrip("\\")
        target = (self.root_dir / key).resolve()
        # 确保在 root_dir 内
        if not str(target).startswith(str(self.root_dir)):
            raise ValueError(f"Invalid storage key: {key}")
        return target

    async def upload(self, key: str, data: bytes) -> None:
        path = self._resolve_path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(path, "wb") as f:
            await f.write(data)

    async def download(self, key: str) -> bytes:
        path = self._resolve_path(key)
        async with aiofiles.open(path, "rb") as f:
            return await f.read()

    async def exists(self, key: str) -> bool:
        path = self._resolve_path(key)
        return path.exists() and path.is_file()

    async def delete(self, key: str) -> None:
        path = self._resolve_path(key)
        if path.exists():
            path.unlink()

    async def get_size(self, key: str) -> int:
        path = self._resolve_path(key)
        return path.stat().st_size

    def get_local_path(self, key: str) -> Optional[str]:
        path = self._resolve_path(key)
        return str(path) if path.exists() else None
