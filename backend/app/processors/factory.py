"""
文档处理器工厂
根据文件类型自动选择对应的专属处理器。

设计原则：每次调用 get_processor 都返回新实例，确保：
1. 并发安全（多请求互不干扰）
2. 状态隔离（每个请求有独立的 parser/chunker/config 缓存）
3. 资源可控（用完即 close，不会常驻内存）
"""

from typing import Dict, Callable, Optional

from .base import BaseDocumentProcessor
from app.core.exceptions import UnsupportedFileTypeError
from app.core.logging import get_logger

logger = get_logger(__name__)

# 类型 → 处理器构造函数的映射（延迟导入，避免循环依赖）
_provider_factories: Dict[str, Callable[[str], BaseDocumentProcessor]] = {}
_initialized = False


def _register_factories() -> None:
    """注册所有处理器的构造函数（延迟导入）"""
    global _initialized
    if _initialized:
        return

    from .excel_processor import ExcelProcessor
    from .csv_processor import CSVProcessor
    from .pdf_processor import PDFProcessor
    from .docx_processor import DOCXProcessor
    from .ppt_processor import PPTProcessor
    from .txt_processor import TXTProcessor
    from .markdown_processor import MarkdownProcessor
    from .html_processor import HTMLProcessor
    from .image_processor import ImageProcessor
    from .audio_processor import AudioProcessor
    from .video_processor import VideoProcessor

    # 每种文件类型对应一个构造器 lambda，调用时才创建实例
    factories = {
        # 表格类
        "xlsx": lambda ft: ExcelProcessor(ft),
        "xls": lambda ft: ExcelProcessor(ft),
        "excel": lambda ft: ExcelProcessor(ft),
        "csv": lambda ft: CSVProcessor(ft),
        # PDF
        "pdf": lambda ft: PDFProcessor(ft),
        # Word
        "docx": lambda ft: DOCXProcessor(ft),
        "doc": lambda ft: DOCXProcessor(ft),
        # PowerPoint
        "pptx": lambda ft: PPTProcessor(ft),
        "ppt": lambda ft: PPTProcessor(ft),
        "powerpoint": lambda ft: PPTProcessor(ft),
        # 文本类
        "txt": lambda ft: TXTProcessor(ft),
        "md": lambda ft: MarkdownProcessor(ft),
        "markdown": lambda ft: MarkdownProcessor(ft),
        "html": lambda ft: HTMLProcessor(ft),
        "htm": lambda ft: HTMLProcessor(ft),
        # 图片（OCR）
        "png": lambda ft: ImageProcessor(ft),
        "jpg": lambda ft: ImageProcessor(ft),
        "jpeg": lambda ft: ImageProcessor(ft),
        "bmp": lambda ft: ImageProcessor(ft),
        "gif": lambda ft: ImageProcessor(ft),
        "tiff": lambda ft: ImageProcessor(ft),
        "webp": lambda ft: ImageProcessor(ft),
        "image": lambda ft: ImageProcessor(ft),
        # 音频（ASR）
        "mp3": lambda ft: AudioProcessor(ft),
        "wav": lambda ft: AudioProcessor(ft),
        "flac": lambda ft: AudioProcessor(ft),
        "m4a": lambda ft: AudioProcessor(ft),
        "aac": lambda ft: AudioProcessor(ft),
        "ogg": lambda ft: AudioProcessor(ft),
        "wma": lambda ft: AudioProcessor(ft),
        "audio": lambda ft: AudioProcessor(ft),
        # 视频
        "mp4": lambda ft: VideoProcessor(ft),
        "avi": lambda ft: VideoProcessor(ft),
        "mkv": lambda ft: VideoProcessor(ft),
        "mov": lambda ft: VideoProcessor(ft),
        "flv": lambda ft: VideoProcessor(ft),
        "wmv": lambda ft: VideoProcessor(ft),
        "webm": lambda ft: VideoProcessor(ft),
        "video": lambda ft: VideoProcessor(ft),
    }
    _provider_factories.update(factories)
    _initialized = True


_register_factories()


def get_processor(file_type: str) -> BaseDocumentProcessor:
    """
    根据文件类型获取专属处理器（每次调用返回新实例）。

    为什么每次返回新实例？
    - 并发安全：多个请求同时处理相同类型的文件，状态互不干扰
    - 资源可控：用完调用 close() 释放，不会有全局常驻资源
    - 隔离性：每个请求有独立的 chunker/config 缓存

    Args:
        file_type: 文件类型（如 'pdf', 'xlsx', 'docx'）

    Returns:
        对应的处理器实例（新创建的）

    Raises:
        UnsupportedFileTypeError: 不支持的文件类型
    """
    ftype = file_type.lower()
    factory = _provider_factories.get(ftype)
    if not factory:
        raise UnsupportedFileTypeError(file_type)

    # 每次创建新实例
    return factory(ftype)


def get_all_processor_types() -> Dict[str, str]:
    """
    获取所有支持的文件类型及其对应的处理器类名。
    用于展示或调试。
    """
    result = {}
    for ftype, factory in _provider_factories.items():
        # 创建一个临时实例获取类名（只在首次调用时触发，有缓存就好）
        proc = factory(ftype)
        result[ftype] = type(proc).__name__
    return result


def is_supported(file_type: str) -> bool:
    """检查文件类型是否支持"""
    return file_type.lower() in _provider_factories
