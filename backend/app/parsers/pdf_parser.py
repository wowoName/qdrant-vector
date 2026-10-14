"""PDF extraction with shared full/streaming logic and visible OCR failures."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import AsyncIterator
from app.core.config import settings
from app.core.logging import get_logger
from app.utils.text_cleaner import clean_pdf_text
from .base import BaseParser, ParseResult, PageParseResult

logger = get_logger(__name__)

class PDFExtractionError(ValueError):
    pass

class PDFParser(BaseParser):
    def supported_types(self):
        return ["pdf"]

    async def parse(self, file_path: str) -> ParseResult:
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        items = list(self._iter_pages_sync(file_path))
        pages = [item.text for item in items]
        metadata = dict(items[0].metadata) if items else {}
        metadata["ocr_pages"] = [item.page_num + 1 for item in items if item.metadata.get("ocr_used")]
        return ParseResult(text="\n\n".join(pages), pages=pages, metadata=metadata)

    def _extract_page(self, page) -> tuple[str, dict]:
        raw = page.get_text("text", sort=True)
        ocr_used = False
        # Blank pages are legal; pages containing images but no text are not.
        # Sparse text plus a large image often means a scanned page with footer.
        image_area = sum(rect.width * rect.height for image in page.get_images(full=True)
                         for rect in page.get_image_rects(image[0]))
        likely_scan = bool(page.get_images()) and (
            not raw.strip() or (len(raw.strip()) < 80 and image_area > page.rect.width * page.rect.height * 0.5)
        )
        if likely_scan:
            if not settings.pdf_ocr_enabled:
                raise PDFExtractionError(f"PDF 第 {page.number + 1} 页需要 OCR，但 PDF_OCR_ENABLED 已关闭")
            ocr_text = self._ocr_page(page)
            if not ocr_text.strip():
                raise PDFExtractionError(f"PDF 第 {page.number + 1} 页为扫描页，OCR 未识别出文字，不能建立完整索引")
            # Preserve native text too; don't replace sparse original content.
            raw = raw + "\n" + ocr_text if raw.strip() else ocr_text
            ocr_used = True
        text = clean_pdf_text(raw)
        table_count = 0
        if settings.pdf_extract_tables and raw.strip() and not ocr_used:
            try:
                tables = page.find_tables().tables
                for table in tables:
                    rows = table.extract()
                    if rows:
                        # Append structured rows without removing native page text.
                        lines = ["【PDF表格】"]
                        headers = [str(value or f"列{i+1}") for i, value in enumerate(rows[0])]
                        lines.append("表头: " + " | ".join(headers))
                        for row in rows[1:]:
                            lines.append("; ".join(f"{header}: {value}" for header, value in zip(headers, row) if value is not None))
                        text += "\n\n" + "\n".join(lines)
                        table_count += 1
            except Exception as exc:
                # Original text survives; log limitations instead of dropping a page.
                logger.warning("PDF table extraction failed on page %s: %s", page.number + 1, exc)
        return text, {"raw_chars": len(raw), "cleaned_chars": len(text), "ocr_used": ocr_used, "table_count": table_count}

    def _ocr_page(self, page) -> str:
        from app.parsers.image_parser import _get_ocr_engine
        engine, backend = _get_ocr_engine()
        if engine is None or backend == "none":
            raise PDFExtractionError(f"PDF 第 {page.number + 1} 页需要 OCR，未安装可用 OCR 引擎")
        import fitz
        pix = page.get_pixmap(dpi=settings.pdf_ocr_dpi, colorspace=fitz.csRGB, alpha=False)
        try:
            if backend == "rapidocr":
                import numpy as np
                pixels = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
                result, _ = engine(pixels[:, :, ::-1].copy())
                lines = sorted(result or [], key=lambda item: (min(point[1] for point in item[0]), min(point[0] for point in item[0])))
                return "\n".join(item[1] for item in lines)
            import pytesseract
            from PIL import Image
            return pytesseract.image_to_string(Image.frombytes("RGB", (pix.width, pix.height), pix.samples), lang="chi_sim+eng")
        except Exception as exc:
            raise PDFExtractionError(f"PDF 第 {page.number + 1} 页 OCR 失败：{exc}") from exc

    def _iter_pages_sync(self, file_path: str):
        import fitz
        with fitz.open(file_path) as doc:
            if doc.needs_pass:
                raise PDFExtractionError("PDF 已加密，请先提供可读取的未加密文件")
            found_text = False
            for number, page in enumerate(doc):
                text, quality = self._extract_page(page)
                found_text = found_text or bool(text.strip())
                metadata = dict(quality)
                if number == 0:
                    metadata.update({"page_count": len(doc), "title": doc.metadata.get("title", ""), "author": doc.metadata.get("author", "")})
                yield PageParseResult(page_num=number, text=text, metadata=metadata)
            if not found_text:
                raise PDFExtractionError("PDF 未提取到可索引文字，不能将空索引标记为成功")

    async def parse_pages(self, file_path: str) -> AsyncIterator[PageParseResult]:
        # One page in flight; no producer can get stuck on a full queue after
        # cancellation. Close the document only after its active read completes.
        iterator = self._iter_pages_sync(file_path)
        sentinel = object()
        pending = None
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdf-parser")
        loop = asyncio.get_running_loop()
        try:
            while True:
                pending = loop.run_in_executor(executor, next, iterator, sentinel)
                item = await asyncio.shield(pending)
                if item is sentinel:
                    break
                yield item
        finally:
            try:
                if pending is not None and not pending.done():
                    await pending
            finally:
                try:
                    await loop.run_in_executor(executor, iterator.close)
                finally:
                    executor.shutdown(wait=False)

    def total_pages(self, file_path: str) -> int:
        import fitz
        with fitz.open(file_path) as doc:
            return len(doc)
