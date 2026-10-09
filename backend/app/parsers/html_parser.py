"""
HTML 网页解析器
提取正文文本，剥离标签、脚本、样式
"""

import asyncio
import os
from typing import List

from .base import BaseParser, ParseResult
from app.core.logging import get_logger
from app.utils.text_cleaner import normalize_text, clean_whitespace
from app.utils.file_utils import read_text_file

logger = get_logger(__name__)


class HTMLParser(BaseParser):
    """HTML 网页解析器"""

    def supported_types(self) -> List[str]:
        return ["html", "htm"]

    async def parse(self, file_path: str) -> ParseResult:
        logger.info(f"Parsing HTML: {file_path}")
        return await asyncio.to_thread(self._parse_sync, file_path)

    def _parse_sync(self, file_path: str) -> ParseResult:
        try:
            from bs4 import BeautifulSoup
        except ImportError:
            logger.error("beautifulsoup4 not installed, cannot parse HTML")
            return ParseResult(
                text="",
                pages=[],
                metadata={"error": "beautifulsoup4 not installed"},
            )

        # 自动检测编码读取
        html, encoding = read_text_file(file_path)
        logger.debug(f"Detected HTML encoding: {encoding}")

        soup = BeautifulSoup(html, "lxml")

        # 移除脚本、样式、导航、页脚等噪声
        for tag in soup(["script", "style", "noscript", "iframe", "svg", "canvas"]):
            tag.decompose()

        # 提取标题
        title = ""
        if soup.title and soup.title.string:
            title = soup.title.string.strip()

        # 尝试提取正文（优先 article / main / content）
        article = soup.find("article") or soup.find("main") or soup.find(class_="content") or soup.body

        if not article:
            article = soup

        # 按标题结构分"页"
        pages = []
        current_section_lines = []
        current_heading = title or os.path.basename(file_path)

        # 将 heading 作为 section 分隔符
        for element in article.find_all(["h1", "h2", "h3", "p", "li", "td", "th", "pre", "blockquote"]):
            tag_name = element.name
            text = element.get_text(strip=True)
            if not text:
                continue

            if tag_name in ("h1", "h2", "h3"):
                # 保存上一个 section
                if current_section_lines:
                    section_text = f"# {current_heading}\n" + "\n".join(current_section_lines)
                    pages.append(section_text)
                    current_section_lines = []
                level = int(tag_name[1])
                prefix = "#" * level
                current_heading = text
                current_section_lines.append(f"{prefix} {text}")
            else:
                # 段落、列表项等
                if tag_name == "li":
                    current_section_lines.append(f"- {text}")
                elif tag_name in ("td", "th"):
                    current_section_lines.append(text)
                elif tag_name == "pre":
                    current_section_lines.append(f"```\n{text}\n```")
                elif tag_name == "blockquote":
                    current_section_lines.append(f"> {text}")
                else:
                    current_section_lines.append(text)

        # 最后一个 section
        if current_section_lines:
            section_text = f"# {current_heading}\n" + "\n".join(current_section_lines)
            pages.append(section_text)

        # 如果没找到标题结构，按段落分页
        if not pages:
            paragraphs = []
            for p in article.find_all("p"):
                text = p.get_text(strip=True)
                if text:
                    paragraphs.append(text)

            if paragraphs:
                full_text = "\n\n".join(paragraphs)
                pages = [full_text]

        # 全文
        full_text = "\n\n".join(pages)
        full_text = normalize_text(full_text)
        full_text = clean_whitespace(full_text)

        # 重新清洗 pages
        cleaned_pages = [clean_whitespace(normalize_text(p)) for p in pages if p.strip()]

        metadata = {
            "title": title,
            "file_name": os.path.basename(file_path),
            "page_count": len(cleaned_pages),
            "encoding": encoding,
        }

        return ParseResult(
            text=full_text,
            pages=cleaned_pages,
            metadata=metadata,
        )
