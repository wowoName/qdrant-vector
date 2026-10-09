"""
Token 计数工具
"""

from typing import Optional

try:
    import tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _TIKTOKEN_AVAILABLE = False


_default_encoding = None


def _get_encoding():
    """获取默认编码"""
    global _default_encoding
    if _default_encoding is None and _TIKTOKEN_AVAILABLE:
        try:
            _default_encoding = tiktoken.get_encoding("cl100k_base")
        except Exception:
            _default_encoding = None
    return _default_encoding


def count_tokens(text: str, model: Optional[str] = None) -> int:
    """
    估算文本的 token 数
    优先使用 tiktoken，不可用时回退到字符数估算
    """
    if not text:
        return 0

    enc = _get_encoding()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass

    # 回退：中文按 1 字 1 token，英文按 4 字符 1 token 估算
    chinese_chars = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    other_chars = len(text) - chinese_chars
    return chinese_chars + max(1, other_chars // 4)
