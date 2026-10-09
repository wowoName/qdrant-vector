"""
递归字符分块器
按分隔符优先级递归切分，尽量在语义边界处切分

优化说明：
1. 用栈式迭代替代递归，避免大文本下的递归深度问题和函数调用开销
2. 预编译正则分隔符，避免重复编译
3. 优化 _split_by_separator，减少字符串拼接
4. 新增 chunk_stream 生成器接口，支持流式处理大文件，内存占用 O(chunk_size)
5. 合并阶段用更高效的滑动窗口方式
"""

import re
from typing import List, Iterator, Tuple

from app.core.config import settings

from .base import BaseChunker, ChunkItem


class RecursiveChunker(BaseChunker):
    """递归字符分块器（迭代实现 + 流式支持）"""

    # 分隔符优先级（从高到低）
    DEFAULT_SEPARATORS = [
        "\n\n",  # 段落
        "\n",  # 行
        "。|！|？",  # 中文句子结束
        r"\. |! |\? ",  # 英文句子结束
        "；|;",  # 分号
        "，|,",  # 逗号
        " ",  # 空格
        "",  # 字符
    ]

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        min_size: int = None,
        separators: list[str] = None,
    ):
        self.chunk_size = chunk_size or settings.chunk_size
        self.chunk_overlap = chunk_overlap or settings.chunk_overlap
        self.min_size = min_size or settings.chunk_min_size
        self.separators = separators or self.DEFAULT_SEPARATORS

        # 预编译正则分隔符（包含 | 的是正则）
        self._compiled_seps: List[Tuple[str, re.Pattern | None]] = []
        for sep in self.separators:
            if "|" in sep:
                self._compiled_seps.append((sep, re.compile(f"({sep})")))
            else:
                self._compiled_seps.append((sep, None))

    # ------------------------------------------------------------------
    # 标准接口：返回完整 list（向后兼容）
    # ------------------------------------------------------------------
    def chunk(self, text: str) -> list[ChunkItem]:
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [ChunkItem(text=text.strip(), index=0, char_start=0, char_end=len(text))]

        # 先切成小片段（迭代版），再合并
        small_chunks = self._split_iterative(text, 0)
        return self._merge_and_overlap(small_chunks)

    # ------------------------------------------------------------------
    # 流式接口：生成器，逐个 yield ChunkItem，内存占用低
    # ------------------------------------------------------------------
    def chunk_stream(self, text: str) -> Iterator[ChunkItem]:
        """
        流式分块：逐个生成 ChunkItem，不一次性持有全部分块结果。
        适用于大文件，内存占用约为 chunk_size * 2。
        """
        if not text:
            return
        if len(text) <= self.chunk_size:
            yield ChunkItem(text=text.strip(), index=0, char_start=0, char_end=len(text))
            return

        idx = 0
        current_text = ""
        current_start = 0
        overlap_text = ""

        for small in self._split_iterative_stream(text, 0):
            if not small.text.strip():
                continue

            if len(current_text) + len(small.text) <= self.chunk_size:
                if not current_text:
                    current_start = small.char_start
                current_text += small.text
            else:
                # 产出一个完整 chunk
                if current_text.strip() and len(current_text.strip()) >= self.min_size:
                    yield ChunkItem(
                        text=current_text.strip(),
                        index=idx,
                        char_start=current_start,
                        char_end=small.char_start,
                    )
                    idx += 1

                # 计算重叠
                if self.chunk_overlap > 0 and current_text:
                    overlap_text = current_text[-self.chunk_overlap:]
                else:
                    overlap_text = ""

                current_text = overlap_text + small.text
                current_start = small.char_start - len(overlap_text)

        # 最后一个
        if current_text.strip() and len(current_text.strip()) >= self.min_size:
            yield ChunkItem(
                text=current_text.strip(),
                index=idx,
                char_start=current_start,
                char_end=len(text),
            )

    # ------------------------------------------------------------------
    # 核心：迭代版片段切分（替代递归）
    # ------------------------------------------------------------------
    def _split_iterative(self, text: str, offset: int) -> list[ChunkItem]:
        """迭代切分：用栈模拟递归，避免递归深度问题"""
        result: list[ChunkItem] = []
        # 栈元素: (text_segment, segment_offset, sep_index)
        stack: list[Tuple[str, int, int]] = [(text, offset, 0)]

        while stack:
            seg, seg_offset, sep_idx = stack.pop()

            if not seg or len(seg) <= self.chunk_size:
                if seg.strip():
                    result.append(ChunkItem(
                        text=seg,
                        index=0,
                        char_start=seg_offset,
                        char_end=seg_offset + len(seg),
                    ))
                continue

            if sep_idx >= len(self._compiled_seps):
                # 所有分隔符都用完了，按固定大小切
                fixed_chunks = self._fixed_size_split(seg, seg_offset)
                result.extend(fixed_chunks)
                continue

            sep_str, sep_pattern = self._compiled_seps[sep_idx]

            if not sep_str:
                # 空分隔符 = 按字符切（最后兜底），直接走固定大小
                fixed_chunks = self._fixed_size_split(seg, seg_offset)
                result.extend(fixed_chunks)
                continue

            splits = self._split_by_separator_fast(seg, sep_str, sep_pattern)
            if len(splits) <= 1:
                # 这个分隔符切不开，试下一个
                stack.append((seg, seg_offset, sep_idx + 1))
                continue

            # 能切开，把每个片段重新压栈（倒序压栈保证顺序正确）
            current_off = seg_offset + len(seg)
            next_sep_idx = sep_idx + 1
            for part in reversed(splits):
                current_off -= len(part)
                if len(part) > self.chunk_size:
                    stack.append((part, current_off, next_sep_idx))
                elif part.strip():
                    result.append(ChunkItem(
                        text=part,
                        index=0,
                        char_start=current_off,
                        char_end=current_off + len(part),
                    ))

        # 栈是倒序压的，结果需要反序回来
        result.reverse()
        return result

    def _split_iterative_stream(self, text: str, offset: int) -> Iterator[ChunkItem]:
        """
        流式迭代切分：逐个 yield 小片段。
        用显式栈 + 惰性求值，内存占用更低。
        """
        stack: list[Tuple[str, int, int]] = [(text, offset, 0)]

        # 用一个临时列表收集右顺序的结果片段（小规模）
        pending: list[ChunkItem] = []

        while stack:
            seg, seg_offset, sep_idx = stack.pop()

            if not seg or len(seg) <= self.chunk_size:
                if seg.strip():
                    pending.append(ChunkItem(
                        text=seg,
                        index=0,
                        char_start=seg_offset,
                        char_end=seg_offset + len(seg),
                    ))
                continue

            if sep_idx >= len(self._compiled_seps):
                fixed_chunks = self._fixed_size_split(seg, seg_offset)
                pending.extend(fixed_chunks)
                continue

            sep_str, sep_pattern = self._compiled_seps[sep_idx]

            if not sep_str:
                fixed_chunks = self._fixed_size_split(seg, seg_offset)
                pending.extend(fixed_chunks)
                continue

            splits = self._split_by_separator_fast(seg, sep_str, sep_pattern)
            if len(splits) <= 1:
                stack.append((seg, seg_offset, sep_idx + 1))
                continue

            # 先把当前 pending 里的全部 yield（保证顺序）
            if pending:
                # pending 是倒序收集的，需要反序
                for c in reversed(pending):
                    yield c
                pending.clear()

            # 倒序压栈保证弹出顺序正确
            current_off = seg_offset + len(seg)
            next_sep_idx = sep_idx + 1
            for part in reversed(splits):
                current_off -= len(part)
                if len(part) > self.chunk_size:
                    stack.append((part, current_off, next_sep_idx))
                elif part.strip():
                    pending.append(ChunkItem(
                        text=part,
                        index=0,
                        char_start=current_off,
                        char_end=current_off + len(part),
                    ))

        # 最后把剩余的 yield 出去
        if pending:
            for c in reversed(pending):
                yield c

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def _split_by_separator_fast(self, text: str, sep: str, pattern: re.Pattern | None) -> list[str]:
        """按分隔符切分（保留分隔符在片段末尾），预编译版本"""
        if pattern is not None:
            # 正则分隔符
            parts = pattern.split(text)
            # parts 的格式: [text, sep, text, sep, ...]
            # 合并分隔符到前面的片段
            result = []
            n = len(parts)
            i = 0
            while i < n:
                chunk = parts[i]
                if i + 1 < n:
                    chunk += parts[i + 1]
                    i += 2
                else:
                    i += 1
                if chunk:
                    result.append(chunk)
            return result
        else:
            # 普通字符串分隔符
            if not text:
                return []
            parts = text.split(sep)
            if len(parts) == 1:
                return [text] if text else []
            # 前面的片段都加上分隔符
            result = [p + sep for p in parts[:-1]]
            if parts[-1]:
                result.append(parts[-1])
            return result

    def _split_by_separator(self, text: str, sep: str) -> list[str]:
        """旧接口保留（兼容）"""
        idx = next((i for i, (s, _) in enumerate(self._compiled_seps) if s == sep), -1)
        if idx >= 0:
            sep_str, pattern = self._compiled_seps[idx]
            return self._split_by_separator_fast(text, sep_str, pattern)
        # fallback
        if "|" in sep:
            parts = re.split(f"({sep})", text)
            result = []
            for i in range(0, len(parts), 2):
                if i + 1 < len(parts):
                    result.append(parts[i] + parts[i + 1])
                elif parts[i]:
                    result.append(parts[i])
            return result
        else:
            parts = text.split(sep)
            return [p + sep for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])

    def _fixed_size_split(self, text: str, offset: int) -> list[ChunkItem]:
        """固定大小切分（兜底用，轻量版）"""
        chunks = []
        start = 0
        text_len = len(text)
        size = self.chunk_size
        sentence_ends = "。！？.!?\n"

        while start < text_len:
            end = min(start + size, text_len)
            if end < text_len:
                # 向前找句子结束符
                search_end = max(start + self.min_size, end - size // 4)
                split_pos = end
                for i in range(end - 1, search_end - 1, -1):
                    if text[i] in sentence_ends:
                        split_pos = i + 1
                        break
                end = split_pos

            chunk_text = text[start:end]
            if chunk_text.strip():
                chunks.append(ChunkItem(
                    text=chunk_text,
                    index=0,
                    char_start=offset + start,
                    char_end=offset + end,
                ))

            if end >= text_len:
                break
            start = end - self.chunk_overlap
            if start >= text_len or start < end - size:
                start = end

        return chunks

    def _split_recursive(self, text: str, offset: int, separators: list[str]) -> list[ChunkItem]:
        """旧的递归实现保留（兼容，内部转迭代）"""
        return self._split_iterative(text, offset)

    def _merge_and_overlap(self, small_chunks: list[ChunkItem]) -> list[ChunkItem]:
        """将小片段合并为合适大小的 Chunk，并添加重叠"""
        if not small_chunks:
            return []

        merged = []
        current_text = ""
        current_start = small_chunks[0].char_start
        idx = 0

        for sc in small_chunks:
            if not sc.text.strip():
                continue

            if len(current_text) + len(sc.text) <= self.chunk_size:
                if not current_text:
                    current_start = sc.char_start
                current_text += sc.text
            else:
                if current_text.strip() and len(current_text.strip()) >= self.min_size:
                    merged.append(
                        ChunkItem(
                            text=current_text.strip(),
                            index=idx,
                            char_start=current_start,
                            char_end=sc.char_start,
                        )
                    )
                    idx += 1

                # 处理重叠
                overlap_text = ""
                if self.chunk_overlap > 0 and current_text:
                    overlap_text = current_text[-self.chunk_overlap:]

                current_text = overlap_text + sc.text
                current_start = sc.char_start - len(overlap_text)

        # 最后一个
        if current_text.strip() and len(current_text.strip()) >= self.min_size:
            merged.append(
                ChunkItem(
                    text=current_text.strip(),
                    index=idx,
                    char_start=current_start,
                    char_end=small_chunks[-1].char_end,
                )
            )

        return merged

    def chunk_with_pages(self, pages: list[str]) -> list[ChunkItem]:
        all_chunks = []
        global_offset = 0
        chunk_idx = 0

        for page_num, page_text in enumerate(pages):
            if not page_text.strip():
                global_offset += len(page_text)
                continue

            page_chunks = self.chunk(page_text)
            for c in page_chunks:
                c.index = chunk_idx
                c.char_start += global_offset
                c.char_end += global_offset
                c.page_num = page_num
                all_chunks.append(c)
                chunk_idx += 1
            global_offset += len(page_text)

        return all_chunks
