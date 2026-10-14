"""
父子块切片器（Parent-Child Chunker）
解决"切片太小导致语义碎片化、切片太大导致检索不精准"的矛盾。

策略：
- 父块（Parent）：较大的语义单元（如段落、章节），用于提供完整上下文
- 子块（Child）：较小的文本片段，用于向量检索，保证精准命中

检索时：
1. 先用子块做向量检索，找到最相关的小片段
2. 然后回溯到对应的父块，把完整上下文送给 LLM

这样同时保证了检索精度和答案的上下文完整性，
是解决"压缩/碎片化导致向量检索质量下降"的核心方案。
"""

import re
from typing import List, Optional
from dataclasses import dataclass, field

from .base import BaseChunker, ChunkItem
from .recursive_chunker import RecursiveChunker
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ParentChildChunk:
    """父子块结构"""
    parent_text: str                    # 父块全文
    parent_index: int                   # 父块序号
    parent_char_start: int              # 父块起始位置
    parent_char_end: int                # 父块结束位置
    children: List[ChunkItem] = field(default_factory=list)  # 子块列表
    page_num: Optional[int] = None      # 页码


@dataclass
class ParentChildResult:
    """父子块切片结果"""
    parent_chunks: List[ParentChildChunk] = field(default_factory=list)

    def all_child_items(self) -> List[ChunkItem]:
        """获取所有子块（用于向量化和检索）"""
        items = []
        for pc in self.parent_chunks:
            for child in pc.children:
                # 子块上挂父块信息
                child.parent_index = pc.parent_index
                child.parent_text = pc.parent_text
                items.append(child)
        return items


class ParentChildChunker(BaseChunker):
    """
    父子块分块器
    - 父块大小：parent_size（默认 1500 字符）
    - 子块大小：chunk_size（默认 500 字符）
    - 每个子块都会注入父块的标题/上下文前缀，提升向量检索的区分度
    """

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        min_size: int = None,
        parent_size: int = None,
        parent_overlap: int = None,
        context_prefix: bool = True,  # 是否给子块注入父块上下文前缀
    ):
        self.chunk_size = settings.chunk_size if chunk_size is None else chunk_size
        self.chunk_overlap = settings.chunk_overlap if chunk_overlap is None else chunk_overlap
        self.min_size = settings.chunk_min_size if min_size is None else min_size
        self.parent_size = parent_size or getattr(settings, "chunk_parent_size", 1500)
        self.parent_overlap = parent_overlap or getattr(settings, "chunk_parent_overlap", 200)
        self.context_prefix = context_prefix

        # 子块用递归切片器
        self._child_chunker = RecursiveChunker(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            min_size=self.min_size,
        )
        # 父块也用递归切片器（更大的 size）
        self._parent_chunker = RecursiveChunker(
            chunk_size=self.parent_size,
            chunk_overlap=self.parent_overlap,
            min_size=self.min_size,
        )

    def chunk(self, text: str) -> List[ChunkItem]:
        """
        标准 chunk 接口：返回子块列表（兼容原有接口）
        子块的 text 包含上下文前缀，用于提升检索质量
        """
        result = self.chunk_parent_child(text)
        return result.all_child_items()

    def chunk_parent_child(self, text: str) -> ParentChildResult:
        """完整父子块接口"""
        if not text:
            return ParentChildResult()

        # 1. 先切父块
        parent_items = self._parent_chunker.chunk(text)
        if not parent_items:
            return ParentChildResult()

        # 2. 对每个父块切子块
        pc_result = ParentChildResult()

        for p_idx, parent in enumerate(parent_items):
            # 提取父块的标题/首句作为上下文前缀
            prefix = self._extract_context_prefix(parent.text) if self.context_prefix else ""

            # 在父块内部切子块
            child_items = self._child_chunker.chunk(parent.text)

            # 调整子块的 char 位置（相对于全文）
            children = []
            for c_idx, child in enumerate(child_items):
                # 注入上下文前缀（只在 text 里加，不改变 char_start/end）
                if prefix and self.context_prefix:
                    child.text = f"{prefix}\n{child.text}"

                child.char_start = parent.char_start + child.char_start
                child.char_end = parent.char_start + child.char_end
                child.parent_index = p_idx
                child.parent_text = parent.text
                children.append(child)

            pc_chunk = ParentChildChunk(
                parent_text=parent.text,
                parent_index=p_idx,
                parent_char_start=parent.char_start,
                parent_char_end=parent.char_end,
                children=children,
            )
            pc_result.parent_chunks.append(pc_chunk)

        # 重新编号子块
        all_items = pc_result.all_child_items()
        for i, item in enumerate(all_items):
            item.index = i

        return pc_result

    def _extract_context_prefix(self, parent_text: str) -> str:
        """
        从父块中提取上下文前缀（标题 + 首句）
        用于注入子块，提升向量检索时的语义区分度
        """
        lines = [l.strip() for l in parent_text.split("\n") if l.strip()]
        if not lines:
            return ""

        prefix_parts = []

        # 找标题行（# 开头，或明显短且加粗的行）
        for line in lines[:3]:
            if line.startswith("#"):
                prefix_parts.append(line)
                break
            # 短句（< 50 字）可能是标题
            if len(line) < 50 and not line.endswith(("。", ".", "，", ",")):
                prefix_parts.append(f"# {line}")
                break

        # 取首句
        first_sentence = self._first_sentence(parent_text)
        if first_sentence and first_sentence not in prefix_parts:
            if len(first_sentence) < 100:
                prefix_parts.append(f"上下文: {first_sentence}")

        return "\n".join(prefix_parts) if prefix_parts else ""

    def _first_sentence(self, text: str) -> str:
        """提取第一句话"""
        match = re.search(r'([^。！？.!?\n]+[。！？.!?])', text)
        if match:
            return match.group(1).strip()
        # 没有句末标点，取第一行
        for line in text.split("\n"):
            line = line.strip()
            if line:
                return line[:100]
        return text[:100] if text else ""

    def chunk_with_pages(self, pages: List[str]) -> List[ChunkItem]:
        """按页切分，保留页码信息"""
        all_children = []
        global_offset = 0
        chunk_idx = 0

        for page_num, page_text in enumerate(pages):
            if not page_text.strip():
                global_offset += len(page_text)
                continue

            page_result = self.chunk_parent_child(page_text)
            for pc in page_result.parent_chunks:
                pc.page_num = page_num
                for child in pc.children:
                    child.index = chunk_idx
                    child.char_start += global_offset
                    child.char_end += global_offset
                    child.page_num = page_num
                    all_children.append(child)
                    chunk_idx += 1
            global_offset += len(page_text)

        return all_children
