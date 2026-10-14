"""
语义感知切片器（Semantic Chunker）
核心思路：
1. 先按句子/段落粒度切分成小片段
2. 对每个小片段计算 embedding
3. 计算相邻片段的相似度，在相似度低的地方切开（语义边界）
4. 合并片段直到接近 chunk_size

相比固定大小/递归切片，能避免把完整的语义单元切碎，
从而提升向量检索的召回率和准确率。

性能优化：
1. 句子切分用 re.finditer 替代 re.split + text.find，O(n) 复杂度
2. 余弦相似度用 numpy 向量化计算
3. 超大文本支持句子采样，减少 embedding 计算量
4. 默认使用更大的语义窗口（合并句子组后再计算 embedding）
"""

import asyncio
import re
from typing import List, Optional

from .base import BaseChunker, ChunkItem
from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class SemanticChunker(BaseChunker):
    """语义感知分块器（基于 embedding 相似度）"""

    # 句子切分正则（预编译）
    _SENTENCE_PATTERN = re.compile(
        r'[^。！？.!?\n]+[。！？.!?]?[”’」』]?\s*'
    )

    def __init__(
        self,
        chunk_size: int = None,
        chunk_overlap: int = None,
        min_size: int = None,
        similarity_threshold: float = None,
        embedding_provider=None,
        # 性能相关参数
        max_sentences_for_embedding: int = 2000,  # 超过此数量则采样
        sentence_group_size: int = 3,  # 每 N 句合并为一个语义单元再计算 embedding
    ):
        self.chunk_size = settings.chunk_size if chunk_size is None else chunk_size
        self.chunk_overlap = settings.chunk_overlap if chunk_overlap is None else chunk_overlap
        self.min_size = settings.chunk_min_size if min_size is None else min_size
        # 相似度阈值：低于此值认为是语义边界
        self.similarity_threshold = similarity_threshold or getattr(
            settings, "chunk_similarity_threshold", 0.65
        )
        self._embedding = embedding_provider
        self.max_sentences_for_embedding = (
            max_sentences_for_embedding
            if max_sentences_for_embedding is not None
            else getattr(settings, "chunk_max_sentences_for_embedding", 2000)
        )
        self.sentence_group_size = sentence_group_size

    async def _get_embedding(self):
        """获取 embedding 实例（延迟加载）"""
        if self._embedding is None:
            from app.providers.embedding.factory import get_embedding
            self._embedding = await get_embedding()
        return self._embedding

    def chunk(self, text: str) -> List[ChunkItem]:
        """同步接口：内部调用异步"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import threading
                result = []
                def _run():
                    new_loop = asyncio.new_event_loop()
                    try:
                        result.extend(new_loop.run_until_complete(self._async_chunk(text)))
                    finally:
                        new_loop.close()
                t = threading.Thread(target=_run)
                t.start()
                t.join()
                return result
            else:
                return loop.run_until_complete(self._async_chunk(text))
        except RuntimeError:
            return asyncio.run(self._async_chunk(text))

    async def _async_chunk(self, text: str) -> List[ChunkItem]:
        if not text:
            return []
        if len(text) <= self.chunk_size:
            return [ChunkItem(text=text.strip(), index=0, char_start=0, char_end=len(text))]

        # 1. 切分为句子级小片段（O(n) 实现）
        sentences = self._split_sentences_fast(text)
        if len(sentences) <= 1:
            from .fixed_size_chunker import FixedSizeChunker
            return FixedSizeChunker(self.chunk_size, self.chunk_overlap, self.min_size).chunk(text)

        # 2. 决定是否需要采样（避免 embedding 计算量过大）
        use_sampling = len(sentences) > self.max_sentences_for_embedding
        if use_sampling:
            # 采样策略：每隔几句取一个代表句做语义边界检测
            # 边界仍然按原始句子定位
            sample_step = max(1, len(sentences) // self.max_sentences_for_embedding)
            sample_indices = list(range(0, len(sentences), sample_step))
            if sample_indices[-1] != len(sentences) - 1:
                sample_indices.append(len(sentences) - 1)
            sample_texts = [sentences[i]["text"] for i in sample_indices]
        else:
            sample_texts = [s["text"] for s in sentences]

        # 3. 计算 embedding
        try:
            embedding = await self._get_embedding()
            vectors = await embedding.embed_texts(sample_texts)
        except Exception as e:
            logger.warning(f"Semantic embedding failed, falling back to recursive chunker: {e}")
            from .recursive_chunker import RecursiveChunker
            return RecursiveChunker(
                self.chunk_size, self.chunk_overlap, self.min_size
            ).chunk(text)

        # 4. 计算语义边界
        if use_sampling:
            # 基于采样点计算边界，再映射回原句子索引
            sample_boundaries = self._find_semantic_boundaries_fast(vectors)
            # 转换为原句子索引的边界
            boundaries = [sample_indices[b + 1] for b in sample_boundaries
                         if b + 1 < len(sample_indices)]
        else:
            boundary_indices = self._find_semantic_boundaries_fast(vectors)
            boundaries = [b + 1 for b in boundary_indices]  # 边界在这个句子之前切开

        # 5. 基于语义边界 + 大小限制合并句子为 chunk
        chunks = self._merge_sentences_into_chunks(sentences, boundaries, text)

        # 6. 处理重叠
        chunks = self._add_overlap(chunks, text)

        # 7. 编号
        for i, c in enumerate(chunks):
            c.index = i

        return chunks

    def _split_sentences_fast(self, text: str) -> List[dict]:
        """
        高效句子切分：基于 re.finditer，O(n) 复杂度，一次扫描同时拿到文本和位置。
        返回: [{"text": str, "start": int, "end": int}, ...]
        """
        sentences = []
        min_sentence_len = 20  # 最小句子长度

        # 第一遍：粗切分
        raw_sentences = []
        for match in self._SENTENCE_PATTERN.finditer(text):
            t = match.group()
            if t.strip():
                raw_sentences.append({
                    "text": t,
                    "start": match.start(),
                    "end": match.end(),
                })

        if not raw_sentences:
            # 正则没切出来，按换行切
            pos = 0
            for line in text.split("\n"):
                if line.strip():
                    start = text.find(line, pos)
                    if start == -1:
                        start = pos
                    end = start + len(line)
                    raw_sentences.append({"text": line, "start": start, "end": end})
                    pos = end
                else:
                    pos += len(line) + 1

        # 第二遍：合并过短句子
        merged = []
        buffer = None
        for s in raw_sentences:
            if buffer is None:
                buffer = s
            elif len(buffer["text"]) < min_sentence_len:
                buffer = {
                    "text": buffer["text"] + s["text"],
                    "start": buffer["start"],
                    "end": s["end"],
                }
            else:
                merged.append(buffer)
                buffer = s

        if buffer:
            merged.append(buffer)

        return merged

    def _find_semantic_boundaries_fast(self, vectors: List[List[float]]) -> List[int]:
        """
        快速计算语义边界：用 numpy 向量化计算相邻相似度。
        返回边界的索引列表（表示在第 i 个和第 i+1 个向量之间切开）。
        """
        try:
            import numpy as np
            # 转为 numpy 数组
            v = np.array(vectors, dtype=np.float32)
            # 计算相邻向量的余弦相似度
            # norm
            norms = np.linalg.norm(v, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1, norms)  # 避免除零
            v_norm = v / norms
            # 相邻点积
            sims = np.sum(v_norm[:-1] * v_norm[1:], axis=1)

            # 阈值判断
            boundaries = np.where(sims < self.similarity_threshold)[0].tolist()

            # 如果边界太少，用百分位动态调整
            min_boundaries = max(1, len(vectors) // 20)
            if len(boundaries) < min_boundaries:
                # 取相似度最低的 10% 作为边界
                n_boundaries = max(1, len(sims) // 10)
                # 找最小的 n_boundaries 个相似度的索引
                lowest_indices = np.argsort(sims)[:n_boundaries]
                boundaries = sorted(set(boundaries) | set(lowest_indices.tolist()))

            return boundaries

        except ImportError:
            # 没有 numpy，退化到纯 Python 实现
            boundaries = []
            sims_list = []
            for i in range(len(vectors) - 1):
                sim = self._cosine_similarity(vectors[i], vectors[i + 1])
                sims_list.append((sim, i))
                if sim < self.similarity_threshold:
                    boundaries.append(i)

            min_boundaries = max(1, len(vectors) // 20)
            if len(boundaries) < min_boundaries:
                sims_list.sort(key=lambda x: x[0])
                n_boundaries = max(1, len(sims_list) // 10)
                for _, idx in sims_list[:n_boundaries]:
                    if idx not in boundaries:
                        boundaries.append(idx)
                boundaries.sort()

            return boundaries

    def _cosine_similarity(self, v1: List[float], v2: List[float]) -> float:
        """计算余弦相似度（纯 Python，兜底用）"""
        dot = sum(a * b for a, b in zip(v1, v2))
        norm1 = sum(a * a for a in v1) ** 0.5
        norm2 = sum(b * b for b in v2) ** 0.5
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def _merge_sentences_into_chunks(
        self, sentences: List[dict], boundaries: List[int], full_text: str
    ) -> List[ChunkItem]:
        """
        基于语义边界合并句子为 chunk，同时考虑 chunk_size 上限
        boundaries: 在第几个句子之前切开（0-based）
        """
        boundary_set = set(boundaries)
        chunks = []
        current_sentences = []
        current_len = 0
        current_start = sentences[0]["start"]

        for i, sent in enumerate(sentences):
            sent_len = len(sent["text"])

            # 检查加上这个句子是否超过 chunk_size
            if current_len + sent_len > self.chunk_size and current_sentences:
                chunk_text = "".join(s["text"] for s in current_sentences).strip()
                if chunk_text:
                    chunks.append(ChunkItem(
                        text=chunk_text,
                        index=0,
                        char_start=current_start,
                        char_end=current_sentences[-1]["end"],
                    ))
                current_sentences = [sent]
                current_len = sent_len
                current_start = sent["start"]
            elif i in boundary_set and current_len >= self.min_size and current_sentences:
                # 是语义边界且当前块足够大，切开
                chunk_text = "".join(s["text"] for s in current_sentences).strip()
                if chunk_text:
                    chunks.append(ChunkItem(
                        text=chunk_text,
                        index=0,
                        char_start=current_start,
                        char_end=current_sentences[-1]["end"],
                    ))
                current_sentences = [sent]
                current_len = sent_len
                current_start = sent["start"]
            else:
                current_sentences.append(sent)
                current_len += sent_len

        # 最后一个 chunk
        if current_sentences:
            chunk_text = "".join(s["text"] for s in current_sentences).strip()
            if chunk_text:
                chunks.append(ChunkItem(
                    text=chunk_text,
                    index=0,
                    char_start=current_start,
                    char_end=current_sentences[-1]["end"],
                ))
            elif chunks and len(chunk_text) > 0:
                last = chunks[-1]
                merged_text = (last.text + chunk_text).strip()
                last.text = merged_text
                last.char_end = current_sentences[-1]["end"]

        return chunks

    def _add_overlap(self, chunks: List[ChunkItem], full_text: str) -> List[ChunkItem]:
        """
        为 chunk 添加重叠部分（从前面的 chunk 末尾取 overlap 字符）
        语义重叠：尽量从句子边界处开始取重叠
        """
        if not chunks or self.chunk_overlap <= 0:
            return chunks

        sentence_ends = "。！？.!?\n"

        for i in range(1, len(chunks)):
            prev = chunks[i - 1]
            curr = chunks[i]

            # 计算重叠起点：从 prev 末尾往前取 chunk_overlap 字符
            overlap_start = max(prev.char_start, prev.char_end - self.chunk_overlap)

            # 尽量在句子边界处开始重叠
            overlap_text = full_text[overlap_start:prev.char_end]
            # 往前找句子结束符（从中间往后找）
            found = False
            mid = len(overlap_text) // 2
            for j in range(mid, len(overlap_text)):
                if overlap_text[j] in sentence_ends:
                    actual_overlap_start = overlap_start + j + 1
                    if actual_overlap_start < curr.char_start:
                        overlap_content = full_text[actual_overlap_start:curr.char_start]
                        curr.text = (overlap_content + curr.text).strip()
                        curr.char_start = actual_overlap_start
                    found = True
                    break
            if not found:
                # 没找到句子边界，直接加
                overlap_content = full_text[overlap_start:curr.char_start]
                curr.text = (overlap_content + curr.text).strip()
                curr.char_start = overlap_start

        return chunks

    def chunk_with_pages(self, pages: List[str]) -> List[ChunkItem]:
        """按页切分，保留页码信息"""
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
