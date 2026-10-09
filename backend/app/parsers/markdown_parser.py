"""
Markdown 解析器
"""

import asyncio
from typing import List

from .base import BaseParser, ParseResult
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace
from app.utils.file_utils import read_text_file

logger = get_logger(__name__)


class MarkdownParser(BaseParser):
    """Markdown 文档解析器"""

    def supported_types(self) -> List[str]:
        return ["markdown"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing Markdown: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        content, encoding = read_text_file(file_path)

        content = normalize_text(content)
        content = clean_whitespace(content)

        # 按二级标题分块（作为 pages）
        pages = self._split_by_headings(content)

        metadata = self._extract_metadata(content)
        metadata["encoding"] = encoding

        return ParseResult(
            text=content,
            pages=pages,
            metadata=metadata,
        )

    def _split_by_headings(self, text: str) -> List[str]:
        """按二级标题分割内容"""
        import re
        sections = re.split(r"(?m)^##\s+", text)
        # 第一个 section 是一级标题之前的内容
        result = []
        if sections[0].strip():
            result.append(sections[0].strip())
        for sec in sections[1:]:
            result.append("## " + sec.strip())
        return result if result else [text]

    def _extract_metadata(self, text: str) -> dict:
        """提取 Front Matter 元数据"""
        import re
        meta = {}
        if text.startswith("---"):
            match = re.match(r"^---\n(.*?)\n---", text, re.DOTALL)
            if match:
                yaml_text = match.group(1)
                for line in yaml_text.splitlines():
                    if ":" in line:
                        key, value = line.split(":", 1)
                        meta[key.strip()] = value.strip().strip('"').strip("'")
        return meta
