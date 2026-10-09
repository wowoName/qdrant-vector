"""
图片文档处理器（OCR 提取文本）
"""

from .text_processor import TextDocumentProcessor


class ImageProcessor(TextDocumentProcessor):
    """图片专属处理器（OCR 提取文本后切片）"""

    def _create_parser(self):
        from app.parsers.image_parser import ImageParser

        return ImageParser()

    def supported_types(self) -> list[str]:
        return ["png", "jpg", "jpeg", "bmp", "gif", "tiff", "webp", "image"]
