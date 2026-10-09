"""
HTML 文档处理器
"""

from typing import List

from .text_processor import TextDocumentProcessor


class HTMLProcessor(TextDocumentProcessor):
    """HTML (.html / .htm) 专属处理器"""

    def _create_parser(self):
        from app.parsers.html_parser import HTMLParser
        return HTMLParser()

    def supported_types(self) -> List[str]:
        return ["html", "htm"]
