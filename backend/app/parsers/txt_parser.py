"""
TXT 纯文本解析器
"""

import asyncio
from typing import List

from .base import BaseParser, ParseResult
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace
from app.utils.file_utils import read_text_file

logger = get_logger(__name__)


class TXTParser(BaseParser):
    """纯文本解析器"""

    def supported_types(self) -> List[str]:
        return ["txt"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing TXT: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        content, encoding = read_text_file(file_path)
        logger.debug(f"Detected encoding: {encoding}")

        content = normalize_text(content)
        content = clean_whitespace(content)

        # 按段落分"页"
        pages = [p.strip() for p in content.split("\n\n") if p.strip()]

        return ParseResult(
            text=content,
            pages=pages,
            metadata={"encoding": encoding},
        )
