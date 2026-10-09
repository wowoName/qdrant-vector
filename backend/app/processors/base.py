"""
文档处理器基类

统一每种文件类型的"解析 + 切片"接口。
子类只需实现自己的解析逻辑和切片策略选择。
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, AsyncGenerator, Optional

from app.chunkers.base import ChunkItem
from app.parsers.base import ParseResult


@dataclass
class ProcessInfo:
    """处理过程信息（用于进度估算）"""
    total_pages: int = 0          # 总页数/表数
    estimated_chunks: int = 0     # 估算总 chunk 数
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseDocumentProcessor(ABC):
    """
    文档处理器基类

    每个子类负责一种文件类型的完整处理链路：
    解析 → 切片 → 输出 ChunkItem

    子类需要实现：
    - supported_types(): 支持的文件类型
    - parse(): 全量解析
    - chunk(): 全量切片
    - chunk_stream(): 流式切片（可选，默认基于 chunk()）
    - get_info(): 快速获取文件信息（不解析全部内容）
    """

    @abstractmethod
    def supported_types(self) -> List[str]:
        """返回支持的文件类型列表（小写，不含点）"""
        pass

    @abstractmethod
    async def parse(self, file_path: str) -> ParseResult:
        """全量解析文件"""
        pass

    @abstractmethod
    async def chunk(self, file_path: str) -> List[ChunkItem]:
        """全量切片，返回所有 chunk"""
        pass

    async def chunk_stream(self, file_path: str) -> AsyncGenerator[ChunkItem, None]:
        """
        流式切片，逐个 yield chunk。

        默认实现基于 chunk()（非真流式，全量加载后逐个 yield）。
        子类应覆盖此方法以提供真正的流式实现，降低内存峰值。
        """
        chunks = await self.chunk(file_path)
        for c in chunks:
            yield c

    @abstractmethod
    def get_info(self, file_path: str) -> ProcessInfo:
        """
        快速获取文件信息（尽量不解析全部内容）。
        用于进度估算和处理模式决策。
        """
        pass

    def close(self):
        """释放资源（可选）"""
        pass
