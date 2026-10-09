"""
解析器工厂
"""

from typing import Dict, Optional, AsyncIterator

from .base import BaseParser, ParseResult, PageParseResult
from .pdf_parser import PDFParser
from .txt_parser import TXTParser
from .markdown_parser import MarkdownParser
from .docx_parser import DOCXParser
from .excel_parser import ExcelParser
from .csv_parser import CSVParser
from .image_parser import ImageParser
from .video_parser import VideoParser
from .ppt_parser import PPTParser
from .html_parser import HTMLParser
from .audio_parser import AudioParser
from app.core.exceptions import UnsupportedFileTypeError
from app.core.logging import get_logger

logger = get_logger(__name__)

_parsers: Dict[str, BaseParser] = {}


def _register_parsers() -> None:
    """注册所有解析器"""
    instances = [
        PDFParser(),
        TXTParser(),
        MarkdownParser(),
        DOCXParser(),
        ExcelParser(),
        CSVParser(),
        ImageParser(),
        VideoParser(),
        PPTParser(),
        HTMLParser(),
        AudioParser(),
    ]
    for parser in instances:
        for ftype in parser.supported_types():
            _parsers[ftype] = parser


_register_parsers()


def get_parser(file_type: str) -> BaseParser:
    """根据文件类型获取解析器"""
    parser = _parsers.get(file_type.lower())
    if not parser:
        raise UnsupportedFileTypeError(file_type)
    return parser


async def parse_file(file_path: str, file_type: str) -> ParseResult:
    """解析文件的便捷函数（全量）"""
    parser = get_parser(file_type)
    return await parser.parse(file_path)


async def parse_file_pages(file_path: str, file_type: str) -> AsyncIterator[PageParseResult]:
    """
    逐页流式解析文件的便捷函数。
    对于支持分页的文件（PDF/PPT/DOCX 等），按页 yield，内存占用更低。
    对于不分页的文件（TXT/Markdown 等），会一次性解析后 yield 单页。
    """
    parser = get_parser(file_type)
    async for page in parser.parse_pages(file_path):
        yield page


def get_total_pages(file_path: str, file_type: str) -> int:
    """获取文件总页数（快速，不解析内容）"""
    try:
        parser = get_parser(file_type)
        return parser.total_pages(file_path)
    except Exception:
        return 0
