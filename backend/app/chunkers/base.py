"""
分块器基类
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class ChunkItem:
    """单个 Chunk"""

    text: str
    index: int
    char_start: int = 0
    char_end: int = 0
    page_num: int = None  # type: ignore
    # 父子块相关（可选）
    parent_index: int = None  # type: ignore
    parent_text: str = None  # type: ignore
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RecordItem:
    """
    结构化记录项（业务型 Excel 的主数据模型）

    一行 = 一条 Record，而不是传统的 Chunk。
    - PostgreSQL 保存完整结构化记录
    - Qdrant 保存语义索引（基于 text 字段做 embedding）
    """

    record_id: str
    """记录唯一 ID，格式：{doc_id}:{sheet_idx}:{row_num}"""

    sheet_name: str
    """所属 Sheet 名称"""

    row_number: int
    """在原始 Excel 中的行号（从 0 开始，0 表示表头后第一行数据）"""

    data: dict[str, Any] = field(default_factory=dict)
    """完整结构化数据，键为列名，值为原始值"""

    text: str = ""
    """用于向量化的文本表达（列名: 值；列名: 值... 格式）"""

    metadata: dict[str, Any] = field(default_factory=dict)
    """额外元数据"""


class BaseChunker(ABC):
    """分块器基类"""

    @abstractmethod
    def chunk(self, text: str) -> list[ChunkItem]:
        """将文本切分为 Chunk 列表"""
        pass

    @abstractmethod
    def chunk_with_pages(self, pages: list[str]) -> list[ChunkItem]:
        """按页切分，保留页码信息"""
        pass

    def chunk_stream(self, text: str) -> Iterator[ChunkItem]:
        """
        流式分块：逐个生成 ChunkItem，内存占用更低。
        默认实现基于 chunk()，子类可覆盖以提供真正的流式实现。
        """
        yield from self.chunk(text)

    def chunk_stream_with_pages(self, pages: list[str]) -> Iterator[ChunkItem]:
        """
        流式按页切分：逐个生成带页码的 ChunkItem。
        """
        global_offset = 0
        chunk_idx = 0
        for page_num, page_text in enumerate(pages):
            if not page_text.strip():
                global_offset += len(page_text)
                continue
            for c in self.chunk_stream(page_text):
                c.index = chunk_idx
                c.char_start += global_offset
                c.char_end += global_offset
                c.page_num = page_num
                yield c
                chunk_idx += 1
            global_offset += len(page_text)
