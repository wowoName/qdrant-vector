"""
PDF 解析器（基于 PyMuPDF）
支持全量解析和逐页流式解析

对于大文件（>100页或文件>5MB），推荐使用 parse_pages() 流式解析，
内存占用仅取决于当前页的大小，而不是整个文档。
"""

import asyncio
import os
from typing import List, AsyncIterator

from .base import BaseParser, ParseResult, PageParseResult
from app.core.logging import get_logger
from app.utils.text_cleaner import clean_pdf_text

logger = get_logger(__name__)


class PDFParser(BaseParser):
    """PDF 文档解析器"""

    def supported_types(self) -> List[str]:
        return ["pdf"]

    async def parse(self, file_path: str) -> ParseResult:
        """解析 PDF 文件（全量）"""
        logger.info(f"Parsing PDF: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        import fitz  # PyMuPDF

        doc = fitz.open(file_path)
        pages_text = []
        metadata = {
            "page_count": len(doc),
            "title": doc.metadata.get("title", ""),
            "author": doc.metadata.get("author", ""),
            "subject": doc.metadata.get("subject", ""),
        }

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")
            pages_text.append(text)

        doc.close()

        # 全文
        full_text = "\n\n".join(pages_text)

        # 文本清洗
        cleaned_text = clean_pdf_text(full_text)
        cleaned_pages = [clean_pdf_text(p) for p in pages_text]

        return ParseResult(
            text=cleaned_text,
            pages=cleaned_pages,
            metadata=metadata,
        )

    async def parse_pages(self, file_path: str) -> AsyncIterator[PageParseResult]:
        """
        逐页流式解析 PDF。
        每解析一页 yield 一页，内存占用 = 单页文本大小。
        对于几百页以上的大 PDF，内存优势非常明显。
        """
        logger.info(f"Streaming PDF pages: {file_path}")
        # 在线程中执行同步迭代，通过队列传递结果
        import threading
        import asyncio

        loop = asyncio.get_running_loop()
        # 用有界队列防止生产者太快导致内存累积
        queue: asyncio.Queue = asyncio.Queue(maxsize=5)
        finished = threading.Event()

        def _producer():
            try:
                import fitz
                from app.utils.text_cleaner import clean_pdf_text

                doc = fitz.open(file_path)
                page_count = len(doc)
                logger.debug(f"PDF opened: {page_count} pages")

                for page_num in range(page_count):
                    page = doc[page_num]
                    raw_text = page.get_text("text")
                    cleaned = clean_pdf_text(raw_text)

                    metadata = {}
                    if page_num == 0:
                        metadata = {
                            "page_count": page_count,
                            "title": doc.metadata.get("title", ""),
                            "author": doc.metadata.get("author", ""),
                            "subject": doc.metadata.get("subject", ""),
                        }

                    result = PageParseResult(
                        page_num=page_num,
                        text=cleaned,
                        metadata=metadata,
                    )
                    # 同步调用 asyncio loop 的 put
                    asyncio.run_coroutine_threadsafe(queue.put(result), loop).result()

                doc.close()
            except Exception as e:
                # 把异常传递给消费者
                asyncio.run_coroutine_threadsafe(queue.put(e), loop).result()
            finally:
                finished.set()
                # 放一个哨兵值表示结束
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

        thread = threading.Thread(target=_producer, daemon=True)
        thread.start()

        while True:
            item = await queue.get()
            if item is None:
                break
            if isinstance(item, Exception):
                raise item
            yield item

        thread.join()

    def total_pages(self, file_path: str) -> int:
        """获取 PDF 总页数（快速获取，不解析内容）"""
        try:
            import fitz
            doc = fitz.open(file_path)
            count = len(doc)
            doc.close()
            return count
        except Exception:
            return 0
