"""
固定大小分块器
"""

from typing import List, Iterator

from .base import BaseChunker, ChunkItem
from app.core.config import settings


class FixedSizeChunker(BaseChunker):
    """固定字符数分块"""

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        min_size: int = None,
    ):
        self.chunk_size = settings.chunk_size if chunk_size is None else chunk_size
        self.chunk_overlap = settings.chunk_overlap if chunk_overlap is None else chunk_overlap
        self.min_size = settings.chunk_min_size if min_size is None else min_size
        self._sentence_ends = "。！？.!?\n"

    def chunk(self, text: str) -> List[ChunkItem]:
        return list(self.chunk_stream(text))

    def chunk_stream(self, text: str) -> Iterator[ChunkItem]:
        """流式固定大小分块"""
        if not text:
            return
        if self.chunk_size < 1 or not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("overlap must be smaller than positive chunk_size")
        if len(text) <= self.min_size:
            yield ChunkItem(text=text, index=0, char_start=0, char_end=len(text))
            return

        start = 0
        idx = 0
        text_len = len(text)
        size = self.chunk_size
        sentence_ends = self._sentence_ends

        while start < text_len:
            end = min(start + size, text_len)

            # 尝试在边界处找自然分割点（句子末尾）
            if end < text_len:
                search_end = max(start + self.min_size, end - size // 4)
                split_pos = end
                for i in range(end - 1, search_end - 1, -1):
                    if text[i] in sentence_ends:
                        split_pos = i + 1
                        break
                end = split_pos

            chunk_text = text[start:end]
            if chunk_text.strip():
                yield ChunkItem(
                    text=chunk_text,
                    index=idx,
                    char_start=start,
                    char_end=end,
                )
                idx += 1

            if end >= text_len:
                break
            start = max(start + 1, end - self.chunk_overlap)

    def _find_split_point(self, text: str, start: int, end: int) -> int:
        """在 [start, end] 范围内找最近的句子结束位置"""
        for i in range(end - 1, start - 1, -1):
            if text[i] in self._sentence_ends:
                return i + 1
        return end

    def chunk_with_pages(self, pages: List[str]) -> List[ChunkItem]:
        return list(self.chunk_stream_with_pages(pages))
