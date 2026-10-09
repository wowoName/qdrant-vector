"""
通用文本型文档处理器
适合 DOCX / PPT / TXT / Markdown / HTML 等"解析后是纯文本"的文件类型。
封装通用的解析 + 文本切片逻辑，子类只需提供 parser 实例。

每个处理器自动从 per_type_chunk_config 读取该文件类型的专属切片配置，
不需要全局统一一套参数。
"""

import asyncio
from typing import List, Iterator

from .base import BaseDocumentProcessor, ProcessInfo
from app.parsers.base import BaseParser, ParseResult
from app.chunkers.base import BaseChunker, ChunkItem
from app.core.logging import get_logger
from app.core.config import settings, get_chunk_config

logger = get_logger(__name__)


class TextDocumentProcessor(BaseDocumentProcessor):
    """
    通用文本型文档处理器

    子类只需实现 _create_parser() 返回对应的 parser 即可。
    自动拥有：全量解析、全量切片、流式切片、信息估算 等能力。
    自动从 per_type_chunk_config 读取该类型的专属切片参数。
    """

    def __init__(self, file_type: str | None = None, chunk_strategy: str | None = None):
        """
        Args:
            file_type: 主文件类型（用于读取 per-type 配置）。
                       None 则从 parser 的第一个 supported type 推断。
            chunk_strategy: 可选，强制覆盖切片策略
        """
        self._parser: BaseParser | None = None
        self._chunker: BaseChunker | None = None
        self._file_type = file_type
        self._chunk_strategy_override = chunk_strategy
        self._config_cache: dict | None = None

    def _create_parser(self) -> BaseParser:
        """子类必须实现，返回对应的 parser 实例"""
        raise NotImplementedError

    def _get_parser(self) -> BaseParser:
        if self._parser is None:
            self._parser = self._create_parser()
        return self._parser

    def _get_chunk_config(self) -> dict:
        """获取该处理器的切片配置（带缓存）"""
        if self._config_cache is None:
            ftype = self._file_type
            if not ftype:
                types = self._get_parser().supported_types()
                ftype = types[0] if types else None
            self._config_cache = get_chunk_config(ftype)
            # 如果有策略覆盖
            if self._chunk_strategy_override:
                self._config_cache["strategy"] = self._chunk_strategy_override
        return self._config_cache

    def _get_chunker(self) -> BaseChunker:
        """创建切片器，使用该文件类型的专属参数"""
        if self._chunker is None:
            from app.chunkers.factory import get_chunker

            cfg = self._get_chunk_config()
            strategy = cfg.get("strategy", settings.chunk_strategy)

            # 创建切片器
            chunker = get_chunker(strategy)

            # 动态设置 size / overlap（如果 chunker 支持这些属性）
            if "size" in cfg and hasattr(chunker, "chunk_size"):
                try:
                    chunker.chunk_size = cfg["size"]
                except Exception:
                    pass
            if "overlap" in cfg and hasattr(chunker, "chunk_overlap"):
                try:
                    chunker.chunk_overlap = cfg["overlap"]
                except Exception:
                    pass
            if "min_size" in cfg and hasattr(chunker, "chunk_min_size"):
                try:
                    chunker.chunk_min_size = cfg["min_size"]
                except Exception:
                    pass

            self._chunker = chunker
        return self._chunker

    # ------------------------------------------------------------------
    # 基类接口实现
    # ------------------------------------------------------------------
    def supported_types(self) -> List[str]:
        return self._get_parser().supported_types()

    async def parse(self, file_path: str) -> ParseResult:
        """全量解析"""
        return await self._get_parser().parse(file_path)

    async def chunk(self, file_path: str) -> List[ChunkItem]:
        """全量切片"""
        chunker = self._get_chunker()
        parse_result = await self.parse(file_path)

        if parse_result.pages and len(parse_result.pages) > 1:
            return chunker.chunk_with_pages(parse_result.pages)
        return chunker.chunk(parse_result.text)

    async def chunk_stream(self, file_path: str) -> Iterator[ChunkItem]:
        """流式切片"""
        chunker = self._get_chunker()
        parse_result = await self.parse(file_path)

        if parse_result.pages and len(parse_result.pages) > 1:
            for c in chunker.chunk_stream_with_pages(parse_result.pages):
                yield c
        else:
            for c in chunker.chunk_stream(parse_result.text):
                yield c

    def get_info(self, file_path: str) -> ProcessInfo:
        """获取文件信息（页数/估算 chunk 数）"""
        total_pages = self._get_parser().total_pages(file_path)

        # 更准确的估算：根据文件大小和 chunk size 估算
        cfg = self._get_chunk_config()
        chunk_size = cfg.get("size", settings.chunk_size)

        if total_pages > 0:
            # 每页按 1500 字估算，除以 chunk_size
            est = max(1, int(total_pages * 1500 / max(chunk_size, 50)))
        else:
            # 无法获取页数时，粗略估算
            est = 50

        return ProcessInfo(
            total_pages=total_pages,
            estimated_chunks=est,
            metadata={"chunk_size": chunk_size},
        )

    def close(self):
        self._chunker = None
        self._parser = None
        self._config_cache = None
