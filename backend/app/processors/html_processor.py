"""
HTML 文档处理器
"""

from .text_processor import TextDocumentProcessor


class HTMLProcessor(TextDocumentProcessor):
    """HTML (.html / .htm) 专属处理器"""

    def _create_parser(self):
        from app.parsers.html_parser import HTMLParser

        return HTMLParser()

    def supported_types(self) -> list[str]:
        return ["html", "htm"]
