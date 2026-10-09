"""
Embedding 基类
"""

from abc import ABC, abstractmethod
from typing import List, Iterator, AsyncIterator


class BaseEmbedding(ABC):
    """Embedding 模型抽象"""

    @abstractmethod
    async def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """批量文本向量化"""
        pass

    async def embed_texts_stream(self, texts: List[str], batch_size: int = None) -> AsyncIterator[List[List[float]]]:
        """
        流式向量化：分批 yield 结果，降低内存峰值。
        默认实现基于 embed_texts 分批，子类可覆盖以提供原生流式支持。
        
        Yields:
            每批的向量结果列表
        """
        batch_size = batch_size or 32
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            batch_emb = await self.embed_texts(batch)
            yield batch_emb

    @property
    @abstractmethod
    def dimension(self) -> int:
        """向量维度"""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称"""
        pass
