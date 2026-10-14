"""Lossless, ordered boundary-aware text windows, shared by both modes."""
import re
from typing import Iterator
from app.core.config import settings
from .base import BaseChunker, ChunkItem

class RecursiveChunker(BaseChunker):
    DEFAULT_SEPARATORS = ["\n\n", "\n", "。|！|？", r"\. |! |\? ", "；|;", "，|,", " ", ""]

    def __init__(self, chunk_size=None, chunk_overlap=None, min_size=None, separators=None):
        self.chunk_size = settings.chunk_size if chunk_size is None else chunk_size
        self.chunk_overlap = settings.chunk_overlap if chunk_overlap is None else chunk_overlap
        self.min_size = settings.chunk_min_size if min_size is None else min_size
        self.separators = self.DEFAULT_SEPARATORS if separators is None else separators
        self._compiled_seps = [(sep, re.compile(sep) if "|" in sep else None) for sep in self.separators]
        self._validate()

    def _validate(self):
        if self.chunk_size < 1 or not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("chunk_size must be positive and overlap must be smaller than size")

    def chunk(self, text: str) -> list[ChunkItem]:
        return list(self.chunk_stream(text))

    def chunk_stream(self, text: str) -> Iterator[ChunkItem]:
        self._validate()
        start = index = 0
        while start < len(text):
            end = min(start + self.chunk_size, len(text))
            if end < len(text):
                # min_size is a preferred boundary, never a deletion threshold.
                lower = start + min(max(self.min_size, self.chunk_size // 2), self.chunk_size - 1)
                for sep, pattern in self._compiled_seps:
                    if not sep:
                        continue
                    if pattern:
                        matches = list(pattern.finditer(text, lower, end))
                        boundary = matches[-1].end() if matches else -1
                    else:
                        pos = text.rfind(sep, lower, end)
                        boundary = pos + len(sep) if pos >= 0 else -1
                    if boundary > start:
                        end = boundary
                        break
            # Keep exact original slices and offsets; skip only all-whitespace.
            body = text[start:end]
            if body.strip():
                yield ChunkItem(text=body, index=index, char_start=start, char_end=end)
                index += 1
            if end == len(text):
                break
            start = max(start + 1, end - self.chunk_overlap)

    def chunk_with_pages(self, pages: list[str]) -> list[ChunkItem]:
        return list(self.chunk_stream_with_pages(pages))
