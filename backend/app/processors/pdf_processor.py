"""
PDF 文档处理器
专属处理 .pdf 文件，解析 + 文本切片一体化。
支持逐页流式处理，大文件内存占用低。
"""

from collections.abc import AsyncIterator

from app.chunkers.base import ChunkItem
from app.core.config import get_chunk_config, settings
from app.core.logging import get_logger
from app.parsers.base import ParseResult
from app.parsers.pdf_parser import PDFParser

from .base import BaseDocumentProcessor, ProcessInfo

logger = get_logger(__name__)


class PDFProcessor(BaseDocumentProcessor):
    """
    PDF 专属处理器

    解析 + 文本切片一体化，底层使用：
    - PyMuPDF (fitz): PDF 解析
    - RecursiveChunker: 递归文本切片（默认）
    - 支持页级流式：逐页解析 + 逐页切片，内存最优
    """

    def __init__(self, file_type: str | None = None, chunk_strategy: str | None = None):
        self._parser = PDFParser()
        self._chunker = None
        self._file_type = file_type or "pdf"
        self._chunk_strategy_override = chunk_strategy
        self._config_cache: dict | None = None

    def supported_types(self) -> list[str]:
        return ["pdf"]

    # ------------------------------------------------------------------
    # 解析接口
    # ------------------------------------------------------------------
    async def parse(self, file_path: str) -> ParseResult:
        """全量解析 PDF"""
        return await self._parser.parse(file_path)

    # ------------------------------------------------------------------
    # 切片接口
    # ------------------------------------------------------------------
    async def chunk(self, file_path: str) -> list[ChunkItem]:
        """全量切片"""
        chunker = self._get_chunker()
        parse_result = await self.parse(file_path)

        if parse_result.pages:
            return chunker.chunk_with_pages(parse_result.pages)
        return chunker.chunk(parse_result.text)

    async def chunk_stream(self, file_path: str) -> AsyncIterator[ChunkItem]:
        """
        流式切片：逐页解析 → 逐页切片 → 逐个 yield chunk。
        内存峰值仅取决于当前页 + 当前 chunk，与文件大小几乎无关。
        """
        chunker = self._get_chunker()
        global_offset = 0
        chunk_idx = 0

        async for page in self._parser.parse_pages(file_path):
            if not page.text.strip():
                global_offset += len(page.text)
                continue

            for c in chunker.chunk_stream(page.text):
                c.index = chunk_idx
                c.char_start += global_offset
                c.char_end += global_offset
                c.page_num = page.page_num
                yield c
                chunk_idx += 1

            global_offset += len(page.text)

    # ------------------------------------------------------------------
    # 信息获取
    # ------------------------------------------------------------------
    def get_info(self, file_path: str) -> ProcessInfo:
        """快速获取 PDF 总页数 + 估算 chunk 数"""
        total_pages = self._parser.total_pages(file_path)
        # 更准确的估算：每页按 1500 字估算，除以 chunk_size
        cfg = self._get_chunk_config()
        chunk_size = cfg.get("size", settings.chunk_size)
        est_chunks = max(1, int(total_pages * 1500 / max(chunk_size, 50)))
        return ProcessInfo(
            total_pages=total_pages,
            estimated_chunks=est_chunks,
            metadata={"chunk_size": chunk_size},
        )

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _get_chunk_config(self) -> dict:
        """获取该处理器的切片配置（带缓存）"""
        if self._config_cache is None:
            self._config_cache = get_chunk_config(self._file_type)
            if self._chunk_strategy_override:
                self._config_cache["strategy"] = self._chunk_strategy_override
        return self._config_cache

    def _get_chunker(self):
        """创建切片器，使用 per-type 配置"""
        if self._chunker is None:
            from app.chunkers.factory import get_chunker

            cfg = self._get_chunk_config()
            strategy = cfg.get("strategy", settings.chunk_strategy)
            chunker = get_chunker(strategy, file_type=self._file_type)
            self._chunker = chunker
        return self._chunker

    def close(self):
        self._chunker = None
        self._config_cache = None
