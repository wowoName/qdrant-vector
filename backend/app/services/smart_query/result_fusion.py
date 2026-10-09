"""
多路检索结果融合器

将 SQL / Vector / Keyword 三路检索结果合并、去重、重排序。

融合策略：
- 去重：按 record_id 去重（同一条记录被多路命中时合并得分）
- 加权融合：SQL 1.0 权重 + Vector 原始分 + Keyword 0.8 权重
- RRF（Reciprocal Rank Fusion）：基于排名的融合算法（更鲁棒）
"""

from typing import List, Dict

from .types import (
    MultiRetrievalResult,
    RetrievalHit,
    RetrievalSource,
)
from app.core.logging import get_logger

logger = get_logger(__name__)


class ResultFusion:
    """
    多路检索结果融合器

    支持两种融合策略：
    1. weighted_score - 加权得分融合（简单直接）
    2. rrf - Reciprocal Rank Fusion（基于排名，更鲁棒）
    """

    def __init__(self, strategy: str = "rrf"):
        """
        Args:
            strategy: 融合策略 "rrf" / "weighted_score"
        """
        self.strategy = strategy
        # 各来源权重
        self.weights = {
            RetrievalSource.SQL: 1.0,
            RetrievalSource.VECTOR: 1.0,
            RetrievalSource.KEYWORD: 0.8,
        }
        # RRF 参数
        self.rrf_k = 60

    def fuse(
        self,
        sql_hits: List[RetrievalHit],
        vector_hits: List[RetrievalHit],
        keyword_hits: List[RetrievalHit],
        top_k: int = 20,
    ) -> MultiRetrievalResult:
        """
        融合多路检索结果

        Args:
            sql_hits: SQL 检索结果
            vector_hits: 向量检索结果
            keyword_hits: 关键词检索结果
            top_k: 返回的 top_k 数量

        Returns:
            MultiRetrievalResult，包含各路结果和融合结果
        """
        result = MultiRetrievalResult(
            sql_hits=sql_hits,
            vector_hits=vector_hits,
            keyword_hits=keyword_hits,
        )

        # 执行融合
        if self.strategy == "rrf":
            merged = self._fuse_rrf(sql_hits, vector_hits, keyword_hits, top_k)
        else:
            merged = self._fuse_weighted(sql_hits, vector_hits, keyword_hits, top_k)

        result.merged_hits = merged
        return result

    def _fuse_weighted(
        self,
        sql_hits: List[RetrievalHit],
        vector_hits: List[RetrievalHit],
        keyword_hits: List[RetrievalHit],
        top_k: int,
    ) -> List[RetrievalHit]:
        """加权得分融合"""
        # key: 去重键，value: (merged_hit, total_score, hit_count)
        merged_map: Dict[str, tuple] = {}
        # 没有唯一键的 hit，直接追加（不参与去重）
        no_key_hits: List[RetrievalHit] = []

        all_hits = [
            (RetrievalSource.SQL, sql_hits),
            (RetrievalSource.VECTOR, vector_hits),
            (RetrievalSource.KEYWORD, keyword_hits),
        ]

        for source, hits in all_hits:
            weight = self.weights.get(source, 1.0)
            for hit in hits:
                key = self._dedup_key(hit)
                if not key:
                    # 没有唯一键的直接加入（不参与去重）
                    no_key_hits.append(hit)
                    continue

                if key in merged_map:
                    existing, total_score, count = merged_map[key]
                    # 加权合并得分
                    new_score = total_score + (hit.score * weight)
                    # 更新 payload 中的来源信息
                    existing.payload.setdefault("sources", []).append(source.value)
                    merged_map[key] = (existing, new_score, count + 1)
                else:
                    # 新记录
                    new_hit = RetrievalHit(
                        source=RetrievalSource.HYBRID,
                        score=hit.score * weight,
                        record_id=hit.record_id,
                        document_id=hit.document_id,
                        sheet_name=hit.sheet_name,
                        row_number=hit.row_number,
                        content=hit.content,
                        payload={
                            **hit.payload,
                            "sources": [source.value],
                            "original_score": hit.score,
                        },
                        metadata=hit.metadata,
                    )
                    merged_map[key] = (new_hit, hit.score * weight, 1)

        # 按融合得分降序排序
        sorted_items = sorted(
            merged_map.values(),
            key=lambda x: x[1],
            reverse=True,
        )

        # 取 top_k，更新最终得分
        result = []
        for i, (hit, score, count) in enumerate(sorted_items[:top_k]):
            hit.score = min(score / count, 1.0)  # 归一化，最多 1.0
            hit.rank = i + 1
            hit.payload["hit_count"] = count
            result.append(hit)

        # 追加没有唯一键的 hit（不占 top_k 配额的情况下补充）
        remaining = top_k - len(result)
        if remaining > 0 and no_key_hits:
            for i, hit in enumerate(no_key_hits[:remaining]):
                hit.rank = len(result) + i + 1
                result.append(hit)

        return result

    def _fuse_rrf(
        self,
        sql_hits: List[RetrievalHit],
        vector_hits: List[RetrievalHit],
        keyword_hits: List[RetrievalHit],
        top_k: int,
    ) -> List[RetrievalHit]:
        """
        RRF (Reciprocal Rank Fusion) 融合

        公式：score = sum(1 / (k + rank)) for each source
        优点：不需要归一化不同来源的得分，更鲁棒
        """
        rrf_scores: Dict[str, float] = {}
        rrf_hits: Dict[str, RetrievalHit] = {}
        # 没有唯一键的 hit，直接追加（不参与去重）
        no_key_hits: List[RetrievalHit] = []

        all_hits = [
            (RetrievalSource.SQL, sql_hits),
            (RetrievalSource.VECTOR, vector_hits),
            (RetrievalSource.KEYWORD, keyword_hits),
        ]

        for source, hits in all_hits:
            for hit in hits:
                key = self._dedup_key(hit)
                if not key:
                    no_key_hits.append(hit)
                    continue

                rank = hit.rank if hit.rank > 0 else 1
                rrf_score = 1.0 / (self.rrf_k + rank)

                if key in rrf_scores:
                    rrf_scores[key] += rrf_score
                    rrf_hits[key].payload.setdefault("sources", []).append(source.value)
                    rrf_hits[key].payload.setdefault("rrf_ranks", []).append(rank)
                else:
                    new_hit = RetrievalHit(
                        source=RetrievalSource.HYBRID,
                        score=0.0,  # 后面更新
                        record_id=hit.record_id,
                        document_id=hit.document_id,
                        sheet_name=hit.sheet_name,
                        row_number=hit.row_number,
                        content=hit.content,
                        payload={
                            **hit.payload,
                            "sources": [source.value],
                            "rrf_ranks": [rank],
                        },
                        metadata=hit.metadata,
                    )
                    rrf_scores[key] = rrf_score
                    rrf_hits[key] = new_hit

        # 按 RRF 得分降序排序
        sorted_keys = sorted(rrf_scores.keys(), key=lambda k: rrf_scores[k], reverse=True)

        # 取 top_k
        result = []
        for i, key in enumerate(sorted_keys[:top_k]):
            hit = rrf_hits[key]
            hit.score = rrf_scores[key]
            hit.rank = i + 1
            result.append(hit)

        # 追加没有唯一键的 hit（在有剩余配额时补充）
        remaining = top_k - len(result)
        if remaining > 0 and no_key_hits:
            for i, hit in enumerate(no_key_hits[:remaining]):
                hit.rank = len(result) + i + 1
                result.append(hit)

        return result

    def _dedup_key(self, hit: RetrievalHit) -> str:
        """
        生成去重键

        优先用 record_id，其次用 content 哈希。
        """
        if hit.record_id:
            return f"rec:{hit.record_id}"
        if hit.content:
            # 用内容前 100 字符做简单去重
            return f"content:{hash(hit.content[:100])}"
        return ""
