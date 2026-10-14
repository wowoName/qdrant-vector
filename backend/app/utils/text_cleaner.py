"""
文本清洗工具（PDF 文本清洗等）
"""

import re
import unicodedata
from typing import List


def normalize_text(text: str) -> str:
    """Unicode 标准化 + 不可见字符清理"""
    # NFKC 标准化：全角转半角，兼容字符归一化
    text = unicodedata.normalize("NFKC", text)

    # 移除零宽字符和其他不可见控制字符（保留换行、制表符）
    text = re.sub(r"[\u200b-\u200f\u202a-\u202e\ufeff\uf000-\uf0ff]", "", text)

    # 移除 NULL 字符
    text = text.replace("\x00", "")

    return text


def clean_whitespace(text: str) -> str:
    """清理空白字符"""
    # 多个空格/制表符合并为一个
    text = re.sub(r"[ \t]+", " ", text)
    # 行首尾空白
    lines = [line.strip() for line in text.splitlines()]
    # 连续空行压缩为最多 2 个
    result = []
    empty_count = 0
    for line in lines:
        if not line:
            empty_count += 1
            if empty_count <= 2:
                result.append("")
        else:
            empty_count = 0
            result.append(line)
    return "\n".join(result).strip()


def remove_headers_footers(lines: List[str], threshold: float = 0.6, context_lines: int = 3) -> List[str]:
    """
    检测并移除页眉页脚
    基于"重复出现在每页顶部/底部"的特征
    """
    if len(lines) < context_lines * 4:
        return lines

    # 统计行出现频率
    from collections import Counter
    top_lines = []
    bottom_lines = []

    # 模拟分页：按空行分组，假设每 50 行一页（粗略）
    page_size = max(30, len(lines) // 10)
    pages = [lines[i:i + page_size] for i in range(0, len(lines), page_size)]

    for page in pages:
        if len(page) >= context_lines * 2:
            top_lines.extend(page[:context_lines])
            bottom_lines.extend(page[-context_lines:])

    top_counter = Counter(top_lines)
    bottom_counter = Counter(bottom_lines)
    num_pages = len(pages)

    header_lines = set()
    footer_lines = set()

    for line, count in top_counter.items():
        if line.strip() and count / num_pages >= threshold:
            header_lines.add(line)

    for line, count in bottom_counter.items():
        if line.strip() and count / num_pages >= threshold:
            footer_lines.add(line)

    # 移除纯数字行（页码）
    result = []
    for line in lines:
        stripped = line.strip()
        # 跳过页码行
        if re.match(r"^\d+$", stripped) and len(stripped) <= 4:
            continue
        if line in header_lines or line in footer_lines:
            continue
        result.append(line)

    return result


def fix_line_breaks(text: str) -> str:
    """
    修复 PDF 提取文本的断行问题
    - 中文断行：非终止符结尾就拼接
    - 英文断行：去连字符 + 空格连接
    """
    lines = text.splitlines()
    paragraphs = []
    current = []

    def is_chinese_char(c: str) -> bool:
        return "\u4e00" <= c <= "\u9fff"

    def should_join(prev: str, curr: str) -> bool:
        if not prev or not curr:
            return False
        prev_end = prev[-1]
        curr_start = curr[0]

        # 中文 - 中文
        if is_chinese_char(prev_end) and is_chinese_char(curr_start):
            return prev_end not in "。！？；：…"

        # 中文 - 英文（通常是拼接）
        if is_chinese_char(prev_end) and curr_start.isalpha():
            return True

        # 英文 - 中文
        if prev_end.isalpha() and is_chinese_char(curr_start):
            return True

        # 英文 - 英文
        if prev_end.isalpha() and curr_start.isalpha():
            # 连字符处理
            if prev.endswith("-"):
                return True
            # 前一行不是句末标点，继续
            return prev_end not in ".!?;:\"'"

        # 数字或其他，默认拼接
        return prev_end not in ".!?。！？"

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                paragraphs.append("".join(current))
                current = []
            continue

        if current and should_join(current[-1], stripped):
            # 处理英文连字符
            if current[-1].endswith("-") and stripped[0].isalpha():
                current[-1] = current[-1][:-1] + stripped
            elif is_chinese_char(current[-1][-1]) or is_chinese_char(stripped[0]):
                current[-1] = current[-1] + stripped
            else:
                current[-1] = current[-1] + " " + stripped
        else:
            current.append(stripped)

    if current:
        paragraphs.append("".join(current))

    return "\n\n".join(paragraphs)


def clean_pdf_text(text: str) -> str:
    """Conservative PDF cleaning: keep numbers, repeated content and line layout.

    Page furniture cannot be inferred from a single page's text. NFKC also
    changes scientific superscripts/units. Never delete or join content here.
    """
    text = unicodedata.normalize("NFC", text).replace("\x00", "")
    text = re.sub(r"[\u200b\ufeff]", "", text)
    return text.strip()
