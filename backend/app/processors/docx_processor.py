"""
Word 文档处理器（DOCX / DOC）
"""

from .text_processor import TextDocumentProcessor


class DOCXProcessor(TextDocumentProcessor):
    """Word (.docx / .doc) 专属处理器"""

    def _create_parser(self):
        from app.parsers.docx_parser import DOCXParser

        return DOCXParser()

    def supported_types(self) -> list[str]:
        return ["docx", "doc"]
