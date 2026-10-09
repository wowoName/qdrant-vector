"""
哈希工具函数
"""

import hashlib
import uuid
from typing import Optional


def sha256_bytes(data: bytes) -> str:
    """计算字节的 SHA256"""
    return hashlib.sha256(data).hexdigest()


def sha256_file(file_path: str, chunk_size: int = 8192) -> str:
    """计算文件的 SHA256（流式）"""
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    """计算文本的 SHA256"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_id() -> str:
    """生成 UUID"""
    return str(uuid.uuid4())


def short_id(prefix: str = "") -> str:
    """生成短 ID（8 位）"""
    s = uuid.uuid4().hex[:8]
    return f"{prefix}_{s}" if prefix else s
