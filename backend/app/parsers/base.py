"""
文档解析器基类
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, AsyncIterator, Iterator


@dataclass
class ParseResult:
    """解析结果"""
    text: str = ""                     # 全文文本
    pages: List[str] = field(default_factory=list)  # 按页分割的文本
    metadata: Dict[str, Any] = field(default_factory=dict)
    tables: List[List[List[str]]] = field(default_factory=list)  # 提取的表格


@dataclass
class PageParseResult:
    """单页解析结果（用于流式处理）"""
    page_num: int                      # 页码（0-based）
    text: str                          # 本页文本
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseParser(ABC):
    """文档解析器基类"""

    @abstractmethod
    async def parse(self, file_path: str) -> ParseResult:
        """解析文件，返回结构化文本结果（全量）"""
        pass

    @abstractmethod
    def supported_types(self) -> List[str]:
        """支持的文件类型"""
        pass

    async def parse_pages(self, file_path: str) -> AsyncIterator[PageParseResult]:
        """
        逐页流式解析（默认实现基于 parse()，子类可覆盖以提供真正的流式实现）。
        
        对于大文件，优先使用此方法，内存占用更低。
        """
        result = await self.parse(file_path)
        for i, page_text in enumerate(result.pages):
            yield PageParseResult(
                page_num=i,
                text=page_text,
                metadata=result.metadata if i == 0 else {},
            )

    def total_pages(self, file_path: str) -> int:
        """
        获取文件总页数（用于进度估算）。
        默认返回 0 表示未知，子类可覆盖。
        """
        return 0
