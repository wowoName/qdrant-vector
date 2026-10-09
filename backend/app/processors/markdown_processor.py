"""
Markdown 文档处理器
"""

from typing import List

from .text_processor import TextDocumentProcessor


class MarkdownProcessor(TextDocumentProcessor):
    """Markdown (.md / .markdown) 专属处理器"""

    def _create_parser(self):
        from app.parsers.markdown_parser import MarkdownParser
        return MarkdownParser()

    def supported_types(self) -> List[str]:
        return ["markdown", "md"]
