"""
图片解析器（OCR 文字提取）
支持 jpg、jpeg、png、bmp、tiff、webp 等格式
优先使用 rapidocr-onnxruntime（纯离线、中文效果好、无需系统依赖）
"""

import asyncio
import os
from typing import List

from .base import BaseParser, ParseResult
from app.core.config import settings
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace

logger = get_logger(__name__)

# 支持的图片扩展名
IMAGE_EXTENSIONS = {
    "jpg", "jpeg", "png", "bmp", "tiff", "tif", "webp",
}

# OCR 引擎实例缓存
_ocr_engine = None
_ocr_backend = None  # "rapidocr" | "pytesseract" | None


def _get_ocr_engine():
    """
    获取 OCR 引擎实例
    优先级：rapidocr-onnxruntime > pytesseract > None
    """
    global _ocr_engine, _ocr_backend
    if _ocr_engine is not None or _ocr_backend == "none":
        return _ocr_engine, _ocr_backend

    # 1. 尝试 rapidocr-onnxruntime（推荐，纯离线，中文好）
    try:
        from rapidocr_onnxruntime import RapidOCR
        _ocr_engine = RapidOCR()
        _ocr_backend = "rapidocr"
        logger.info("OCR backend: rapidocr-onnxruntime")
        return _ocr_engine, _ocr_backend
    except ImportError:
        logger.debug("rapidocr-onnxruntime not available")

    # 2. 尝试 pytesseract（需要系统安装 Tesseract）
    try:
        import pytesseract  # noqa: F401
        from PIL import Image  # noqa: F401
        _ocr_engine = "pytesseract"
        _ocr_backend = "pytesseract"
        logger.info("OCR backend: pytesseract")
        return _ocr_engine, _ocr_backend
    except ImportError:
        logger.debug("pytesseract not available")

    _ocr_backend = "none"
    logger.warning("No OCR backend available. Install rapidocr-onnxruntime for best results.")
    return None, _ocr_backend


class ImageParser(BaseParser):
    """图片 OCR 解析器"""

    def supported_types(self) -> List[str]:
        return ["image"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing image: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        engine, backend = _get_ocr_engine()
        if engine is None or backend == "none":
            return ParseResult(
                text="",
                pages=[],
                metadata={"error": "No OCR backend available. Install rapidocr-onnxruntime."},
            )

        if backend == "rapidocr":
            return self._parse_rapidocr(file_path, engine)
        elif backend == "pytesseract":
            return self._parse_pytesseract(file_path)

        return ParseResult(text="", pages=[])

    def _parse_rapidocr(self, file_path: str, engine) -> ParseResult:
        """使用 RapidOCR 提取文字"""
        result, _ = engine(file_path)

        if not result:
            return ParseResult(
                text="",
                pages=[],
                metadata={"ocr_engine": "rapidocr", "text_detected": False},
            )

        # result 格式: [[box, text, confidence], ...]
        # 按 y 坐标排序（从上到下），再按 x 排序（从左到右）
        sorted_results = sorted(
            result,
            key=lambda item: (
                # 取左上角 y 的均值作为行基准
                sum(p[1] for p in item[0]) / 4,
                sum(p[0] for p in item[0]) / 4,
            ),
        )

        # 简单行合并：y 坐标接近的视为同一行
        lines = []
        current_line_texts = []
        current_line_y = None
        line_threshold = 15  # 行高容差（像素）

        for box, text, conf in sorted_results:
            avg_y = sum(p[1] for p in box) / 4
            avg_x = sum(p[0] for p in box) / 4

            if current_line_y is None:
                current_line_y = avg_y
                current_line_texts = [(avg_x, text)]
            elif abs(avg_y - current_line_y) < line_threshold:
                current_line_texts.append((avg_x, text))
            else:
                # 保存上一行（按 x 排序）
                current_line_texts.sort(key=lambda x: x[0])
                lines.append(" ".join(t for _, t in current_line_texts))
                current_line_y = avg_y
                current_line_texts = [(avg_x, text)]

        # 最后一行
        if current_line_texts:
            current_line_texts.sort(key=lambda x: x[0])
            lines.append(" ".join(t for _, t in current_line_texts))

        full_text = "\n".join(lines)
        full_text = normalize_text(full_text)
        full_text = clean_whitespace(full_text)

        filename = os.path.basename(file_path)
        return ParseResult(
            text=full_text,
            pages=[full_text] if full_text else [],
            metadata={
                "ocr_engine": "rapidocr",
                "text_detected": True,
                "line_count": len(lines),
                "file_name": filename,
            },
        )

    def _parse_pytesseract(self, file_path: str) -> ParseResult:
        """使用 pytesseract 提取文字"""
        import pytesseract
        from PIL import Image

        img = Image.open(file_path)

        # 尝试识别中英文
        try:
            text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        except pytesseract.TesseractError:
            try:
                text = pytesseract.image_to_string(img, lang="eng")
            except Exception:
                text = ""

        text = normalize_text(text)
        text = clean_whitespace(text)

        filename = os.path.basename(file_path)
        return ParseResult(
            text=text,
            pages=[text] if text else [],
            metadata={
                "ocr_engine": "pytesseract",
                "text_detected": bool(text),
                "file_name": filename,
            },
        )
