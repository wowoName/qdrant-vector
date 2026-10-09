"""
CSV 文档处理器
专属处理 .csv 文件，解析 + 表格切片一体化。
"""

import asyncio
import os
from typing import AsyncGenerator, List

from .base import BaseDocumentProcessor, ProcessInfo
from app.parsers.csv_parser import CSVParser
from app.chunkers.tabular_chunker import TabularChunker
from app.parsers.base import ParseResult
from app.chunkers.base import ChunkItem
from app.core.logging import get_logger
from app.core.config import settings, get_tabular_config

logger = get_logger(__name__)


class CSVProcessor(BaseDocumentProcessor):
    """
    CSV 专属处理器

    解析 + 表格切片一体化，底层使用：
    - csv 模块: 文件读取
    - TabularChunker: 结构化切片（行级/行组/列组/SQL）
    - DuckDB（可选）: 高速 SQL 查询引擎
    """

    def __init__(self, file_type: str | None = None):
        self._parser = CSVParser()
        self._chunker: TabularChunker | None = None
        self._file_type = file_type or "csv"
        self._config_cache: dict | None = None

    def supported_types(self) -> List[str]:
        return ["csv"]

    # ------------------------------------------------------------------
    # 解析接口
    # ------------------------------------------------------------------
    async def parse(self, file_path: str) -> ParseResult:
        """全量解析 CSV"""
        return await self._parser.parse(file_path)

    # ------------------------------------------------------------------
    # 切片接口
    # ------------------------------------------------------------------
    async def chunk(self, file_path: str) -> List[ChunkItem]:
        """全量切片"""
        chunker = self._get_chunker()
        parse_result = await self.parse(file_path)

        if not parse_result.tables or not any(len(t) >= 2 for t in parse_result.tables):
            from app.chunkers.recursive_chunker import RecursiveChunker
            fallback = RecursiveChunker()
            return fallback.chunk(parse_result.text)

        chunks = chunker.chunk_tables(
            tables=parse_result.tables,
            sheet_names=["csv_data"],
            file_path=file_path,
            file_type="csv",
        )
        return chunks

    async def chunk_stream(self, file_path: str) -> AsyncGenerator[ChunkItem, None]:
        """流式切片"""
        chunker = self._get_chunker()
        parse_result = await self.parse(file_path)

        if not parse_result.tables or not any(len(t) >= 2 for t in parse_result.tables):
            from app.chunkers.recursive_chunker import RecursiveChunker
            fallback = RecursiveChunker()
            for c in fallback.chunk_stream(parse_result.text):
                yield c
            return

        for chunk in chunker.chunk_tables_stream(
            tables=parse_result.tables,
            sheet_names=["csv_data"],
            file_path=file_path,
            file_type="csv",
        ):
            yield chunk

    # ------------------------------------------------------------------
    # 信息获取
    # ------------------------------------------------------------------
    def get_info(self, file_path: str) -> ProcessInfo:
        """快速获取 CSV 信息（估算行数）"""
        try:
            # 快速数行数
            row_count = 0
            with open(file_path, "rb") as f:
                for _ in f:
                    row_count += 1
            data_rows = max(0, row_count - 1)  # 减表头

            cfg = self._get_chunk_config()
            mode = cfg.get("mode", "row")
            if mode == "row":
                est_chunks = data_rows
            elif mode == "row_group":
                rows_per = cfg.get("rows_per_chunk") or 10
                est_chunks = max(1, data_rows // rows_per)
            else:
                est_chunks = data_rows

            return ProcessInfo(
                total_pages=1,
                estimated_chunks=max(1, est_chunks),
                metadata={"total_data_rows": data_rows},
            )
        except Exception as e:
            logger.debug(f"Failed to get CSV info: {e}")
            return ProcessInfo(total_pages=1, estimated_chunks=100)

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _get_chunk_config(self) -> dict:
        """获取该处理器的切片配置（带缓存）"""
        if self._config_cache is None:
            self._config_cache = get_tabular_config(self._file_type)
        return self._config_cache

    def _get_chunker(self) -> TabularChunker:
        """获取或创建切片器实例（使用 per-type 配置）"""
        if self._chunker is None:
            cfg = self._get_chunk_config()
            self._chunker = TabularChunker(
                mode=cfg.get("mode", "row"),
                rows_per_chunk=cfg.get("rows_per_chunk"),
                columns_per_group=cfg.get("columns_per_group", 10),
                include_headers=cfg.get("include_headers", True),
                chunk_size=cfg.get("size", settings.chunk_size),
                chunk_overlap=cfg.get("overlap", settings.chunk_overlap),
            )
        return self._chunker

    def close(self):
        if self._chunker:
            self._chunker.close()
            self._chunker = None
        self._config_cache = None
