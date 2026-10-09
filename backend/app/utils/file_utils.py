"""
文件工具函数
"""

import os
import re
from pathlib import Path
from typing import Optional, Tuple


def get_file_extension(filename: str) -> str:
    """获取文件扩展名（小写，不含点）"""
    ext = Path(filename).suffix.lower().lstrip(".")
    return ext


def get_file_type(filename: str) -> Optional[str]:
    """
    根据文件名判断文件类型
    返回的类型名需与解析器的 supported_types 对应
    """
    ext = get_file_extension(filename)
    type_map = {
        # 文档类
        "pdf": "pdf",
        "docx": "docx",
        "doc": "doc",
        "txt": "txt",
        "md": "markdown",
        "markdown": "markdown",
        # 表格类
        "xlsx": "xlsx",
        "xls": "xls",
        "csv": "csv",
        # 演示类
        "pptx": "pptx",
        "ppt": "ppt",
        # 网页类
        "html": "html",
        "htm": "html",
        # 图片类
        "jpg": "image",
        "jpeg": "image",
        "png": "image",
        "bmp": "image",
        "tiff": "image",
        "tif": "image",
        "webp": "image",
        "gif": "image",
        # 视频类
        "mp4": "video",
        "avi": "video",
        "mkv": "video",
        "mov": "video",
        "webm": "video",
        "flv": "video",
        "wmv": "video",
        "m4v": "video",
        # 音频类
        "mp3": "audio",
        "wav": "audio",
        "m4a": "audio",
        "flac": "audio",
        "aac": "audio",
        "ogg": "audio",
        "wma": "audio",
        "opus": "audio",
        "amr": "audio",
    }
    return type_map.get(ext)


def is_supported_file(filename: str) -> bool:
    """判断文件是否支持"""
    return get_file_type(filename) is not None


def sanitize_filename(filename: str) -> str:
    """清洗文件名，移除不安全字符"""
    # 移除路径分隔符和控制字符
    filename = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", filename)
    # 限制长度
    if len(filename) > 200:
        name, ext = os.path.splitext(filename)
        filename = name[: 200 - len(ext)] + ext
    return filename or "unnamed"


def format_file_size(size_bytes: int) -> str:
    """格式化文件大小为可读字符串"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def ensure_dir(path: str) -> None:
    """确保目录存在"""
    Path(path).mkdir(parents=True, exist_ok=True)


def detect_file_encoding(file_path: str, sample_size: int = 100 * 1024) -> str:
    """
    检测文件编码，优先使用 charset_normalizer，回退到常见编码列表尝试
    对中文编码做额外验证，避免误判
    返回检测到的编码名称（默认 utf-8）
    """
    # 先读取样本
    try:
        with open(file_path, "rb") as f:
            raw = f.read(sample_size)
    except Exception:
        return "utf-8"

    if not raw:
        return "utf-8"

    # 优先使用 charset_normalizer 检测
    try:
        from charset_normalizer import from_bytes
        result = from_bytes(raw).best()
        if result and result.encoding:
            detected = result.encoding
            # 验证：如果检测结果能成功解码且包含可打印的中文字符，就接受
            try:
                decoded = raw.decode(detected, errors="replace")
                # 检查是否包含中文汉字（Unicode 中文范围）
                has_chinese = any('\u4e00' <= ch <= '\u9fff' for ch in decoded)
                if has_chinese:
                    # 对于中文内容，验证检测结果是否合理
                    # 如果检测出的是韩文/日文编码但实际是中文，需要回退
                    if detected.lower() in ('cp949', 'euc_kr', 'shift_jis', 'euc_jp'):
                        # 尝试用 GBK 解码，看看是否更合理
                        gbk_decoded = raw.decode('gbk', errors='replace')
                        gbk_chinese = sum(1 for ch in gbk_decoded if '\u4e00' <= ch <= '\u9fff')
                        det_chinese = sum(1 for ch in decoded if '\u4e00' <= ch <= '\u9fff')
                        if gbk_chinese > det_chinese:
                            return 'gbk'
                return detected
            except Exception:
                pass
    except Exception:
        pass

    # 回退：按常见编码顺序尝试（中文编码优先）
    fallback_encodings = [
        "utf-8", "utf-8-sig", "gbk", "gb2312", "gb18030",
        "big5", "utf-16", "utf-16-le", "utf-16-be", "latin-1",
    ]

    for enc in fallback_encodings:
        try:
            raw.decode(enc)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue

    return "utf-8"


def read_text_file(file_path: str, encoding: Optional[str] = None) -> Tuple[str, str]:
    """
    读取文本文件，自动检测编码
    返回 (文本内容, 实际使用的编码)
    """
    if not encoding:
        encoding = detect_file_encoding(file_path)

    try:
        with open(file_path, "r", encoding=encoding, errors="replace") as f:
            content = f.read()
        return content, encoding
    except Exception:
        # 最后兜底：二进制读取 + UTF-8 容错
        try:
            with open(file_path, "rb") as f:
                content = f.read().decode("utf-8", errors="replace")
            return content, "utf-8"
        except Exception as e:
            raise RuntimeError(f"Failed to read text file: {e}")
