"""
纯文本文档处理器（TXT）
"""

from typing import List

from .text_processor import TextDocumentProcessor


class TXTProcessor(TextDocumentProcessor):
    """纯文本 (.txt) 专属处理器"""

    def _create_parser(self):
        from app.parsers.txt_parser import TXTParser
        return TXTParser()

    def supported_types(self) -> List[str]:
        return ["txt"]
