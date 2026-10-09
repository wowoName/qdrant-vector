"""
Embedding 服务工厂
"""

from .base import BaseEmbedding
from .huggingface_embedding import HuggingFaceEmbedding


async def get_embedding() -> BaseEmbedding:
    """获取 Embedding 服务"""
    return await HuggingFaceEmbedding.get_instance()
