"""
PPT 文档处理器（PPT / PPTX）
"""

from .text_processor import TextDocumentProcessor


class PPTProcessor(TextDocumentProcessor):
    """PowerPoint (.ppt / .pptx) 专属处理器"""

    def _create_parser(self):
        from app.parsers.ppt_parser import PPTParser

        return PPTParser()

    def supported_types(self) -> list[str]:
        return ["pptx", "ppt", "powerpoint"]
