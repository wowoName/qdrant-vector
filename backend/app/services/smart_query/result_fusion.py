"""
多路检索结果融合器

将 SQL / Vector / Keyword 三路检索结果合并、去重、重排序。

融合策略：
- 去重：按「文档ID+记录ID」优先，其次按内容 SHA256，确保跨数据源不被错误合并
- 加权融合：按来源权重加权求和，分母为权重之和（非命中次数）
- RRF（Reciprocal Rank Fusion）：加权 RRF，基于排名的融合算法（更鲁棒）

设计原则：
- 输入只读：不修改传入的原始 hit 对象
- 结果独立：所有融合结果都是新建的 RetrievalHit
- 去重可靠：优先业务主键，内容哈希兜底
"""

import copy
import hashlib

from app.core.logging import get_logger

from .types import (
    MultiRetrievalResult,
    RetrievalHit,
    RetrievalSource,
)

logger = get_logger(__name__)


class ResultFusion:
    """
    多路检索结果融合器

    支持两种融合策略：
    1. weighted_score - 加权得分融合（需确保各来源分数尺度可比）
    2. rrf - Reciprocal Rank Fusion（基于排名，更鲁棒，推荐默认）
    """

    def __init__(self, strategy: str = "rrf"):
        """
        Args:
            strategy: 融合策略 "rrf" / "weighted_score"

        Raises:
            ValueError: 不支持的策略
        """
        if strategy not in ("rrf", "weighted_score"):
            raise ValueError(
                f"不支持的融合策略: {strategy}，可选: rrf, weighted_score"
            )
        self.strategy = strategy

        # 各来源权重（加权融合直接作用于分数；RRF 作用于排名贡献）
        self.weights = {
            RetrievalSource.SQL: 1.0,
            RetrievalSource.VECTOR: 1.0,
            RetrievalSource.KEYWORD: 0.8,
        }

        # RRF 参数（k 值越大，排名靠后的结果衰减越慢）
        self.rrf_k = 60

    def fuse(
        self,
        sql_hits: list[RetrievalHit],
        vector_hits: list[RetrievalHit],
        keyword_hits: list[RetrievalHit],
        top_k: int = 20,
    ) -> MultiRetrievalResult:
        """
        融合多路检索结果

        Args:
            sql_hits: SQL 检索结果
            vector_hits: 向量检索结果
            keyword_hits: 关键词检索结果
            top_k: 返回的 top_k 数量（非负整数，0 返回空列表）

        Returns:
            MultiRetrievalResult，包含各路原始结果和融合结果

        Raises:
            ValueError: top_k 为负数
        """
        if top_k < 0:
            raise ValueError(f"top_k 不能为负数: {top_k}")

        result = MultiRetrievalResult(
            sql_hits=sql_hits,
            vector_hits=vector_hits,
            keyword_hits=keyword_hits,
        )

        if top_k == 0:
            result.merged_hits = []
            return result

        sql_count = len(sql_hits)
        vec_count = len(vector_hits)
        kw_count = len(keyword_hits)
        logger.debug(
            f"ResultFusion start: strategy={self.strategy}, "
            f"sql={sql_count}, vector={vec_count}, keyword={kw_count}, top_k={top_k}"
        )

        # 执行融合
        if self.strategy == "rrf":
            merged = self._fuse_rrf(sql_hits, vector_hits, keyword_hits, top_k)
        else:  # weighted_score（__init__ 已校验，不会走到其他值）
            merged = self._fuse_weighted(sql_hits, vector_hits, keyword_hits, top_k)

        result.merged_hits = merged
        logger.debug(
            f"ResultFusion done: merged={len(merged)} hits returned"
        )
        return result

    # ------------------------------------------------------------------
    # 加权得分融合
    # ------------------------------------------------------------------

    def _fuse_weighted(
        self,
        sql_hits: list[RetrievalHit],
        vector_hits: list[RetrievalHit],
        keyword_hits: list[RetrievalHit],
        top_k: int,
    ) -> list[RetrievalHit]:
        """
        加权得分融合。

        公式：score = sum(score_i * w_i) / sum(w_i)
        分母是「命中来源的权重之和」，不是命中次数。

        所有结果都参与统一排序（包括无唯一键的结果），无键结果只占自己的名额，不与其他记录合并。
        """
        # key: 去重键, value: (best_hit, weighted_sum, weight_sum, source_set)
        merged_map: dict[str, tuple] = {}

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
                    # 无唯一键：生成一个临时键（只用于排序，不与其他记录合并）
                    key = f"_nokey:{id(hit)}"
                    is_no_key = True
                else:
                    is_no_key = False

                weighted = hit.score * weight

                if key in merged_map:
                    existing, w_sum, w_sum_total, src_set = merged_map[key]
                    new_w_sum = w_sum + weighted
                    new_w_total = w_sum_total + weight
                    new_src = src_set | {source.value}
                    merged_map[key] = (existing, new_w_sum, new_w_total, new_src)
                else:
                    # 新建融合结果（深拷贝 payload，不修改原始 hit）
                    new_payload = copy.deepcopy(hit.payload or {})
                    new_hit = RetrievalHit(
                        source=RetrievalSource.HYBRID,
                        score=weighted,  # 临时值，最后重新计算
                        record_id=hit.record_id,
                        document_id=hit.document_id,
                        sheet_name=hit.sheet_name,
                        row_number=hit.row_number,
                        content=hit.content,
                        payload=new_payload,
                        metadata=dict(hit.metadata or {}),
                    )
                    merged_map[key] = (new_hit, weighted, weight, {source.value})

        # 计算最终得分并排序
        scored = []
        for key, (hit, w_sum, w_total, src_set) in merged_map.items():
            # 加权平均：sum(score_i * w_i) / sum(w_i)
            final_score = w_sum / w_total if w_total > 0 else 0.0
            hit.score = final_score
            hit.payload["sources"] = sorted(src_set)
            hit.payload["hit_weight_sum"] = round(w_total, 4)
            scored.append((hit, final_score, len(src_set)))

        # 排序：主序按得分降序，次序列按来源数降序（多路命中优先）
        scored.sort(key=lambda x: (-x[1], -x[2]))

        # 取 top_k
        result = []
        for i, (hit, _score, _src_count) in enumerate(scored[:top_k]):
            hit.rank = i + 1
            result.append(hit)

        return result

    # ------------------------------------------------------------------
    # RRF 融合
    # ------------------------------------------------------------------

    def _fuse_rrf(
        self,
        sql_hits: list[RetrievalHit],
        vector_hits: list[RetrievalHit],
        keyword_hits: list[RetrievalHit],
        top_k: int,
    ) -> list[RetrievalHit]:
        """
        加权 RRF (Reciprocal Rank Fusion) 融合。

        公式：score(d) = sum_s [ w_s / (k + rank_s(d)) ]
        - w_s: 来源 s 的权重
        - rank_s(d): 记录 d 在来源 s 的结果列表中的排名（从 1 开始）
        - 同一来源中同一条记录只取最高排名（最小 rank）贡献一次

        优点：不需要归一化不同来源的得分，更鲁棒
        """
        rrf_scores: dict[str, float] = {}
        rrf_hits: dict[str, RetrievalHit] = {}
        rrf_sources: dict[str, set] = {}  # key -> set of source.value
        rrf_ranks: dict[str, dict] = {}   # key -> {source.value: best_rank}

        all_hits = [
            (RetrievalSource.SQL, sql_hits),
            (RetrievalSource.VECTOR, vector_hits),
            (RetrievalSource.KEYWORD, keyword_hits),
        ]

        for source, hits in all_hits:
            weight = self.weights.get(source, 1.0)
            # 同来源内去重：同一条记录只保留最高排名（最小 rank）和对应的原始 hit
            # key -> (best_rank, original_hit)
            seen_in_source: dict[str, tuple] = {}

            for idx, hit in enumerate(hits, start=1):
                key = self._dedup_key(hit)
                if not key:
                    # 无唯一键：生成临时键，参与排序但不合并
                    key = f"_nokey:{id(hit)}"

                # 确定 rank：优先用 hit.rank，缺失/无效时用列表下标
                rank = hit.rank
                if rank is None or rank <= 0:
                    rank = idx

                # 同来源内只保留最好的排名
                if key in seen_in_source:
                    if rank < seen_in_source[key][0]:
                        seen_in_source[key] = (rank, hit)
                    continue
                seen_in_source[key] = (rank, hit)

            # 对该来源所有去重后的记录计算 RRF 贡献
            for key, (best_rank, original_hit) in seen_in_source.items():
                rrf_contrib = weight / (self.rrf_k + best_rank)

                if key in rrf_scores:
                    rrf_scores[key] += rrf_contrib
                    rrf_sources[key].add(source.value)
                    rrf_ranks[key][source.value] = best_rank
                else:
                    new_payload = copy.deepcopy(original_hit.payload or {})
                    new_hit = RetrievalHit(
                        source=RetrievalSource.HYBRID,
                        score=0.0,  # 后面更新
                        record_id=original_hit.record_id,
                        document_id=original_hit.document_id,
                        sheet_name=original_hit.sheet_name,
                        row_number=original_hit.row_number,
                        content=original_hit.content,
                        payload=new_payload,
                        metadata=dict(original_hit.metadata or {}),
                    )
                    rrf_scores[key] = rrf_contrib
                    rrf_hits[key] = new_hit
                    rrf_sources[key] = {source.value}
                    rrf_ranks[key] = {source.value: best_rank}

        # 组装最终结果
        scored = []
        for key, score in rrf_scores.items():
            hit = rrf_hits.get(key)
            if hit is None:
                continue
            hit.score = score
            hit.payload["sources"] = sorted(rrf_sources.get(key, set()))
            hit.payload["rrf_ranks"] = rrf_ranks.get(key, {})
            scored.append((hit, score, len(rrf_sources.get(key, set()))))

        # 排序：主序按 RRF 得分降序，次序列按来源数降序（多路命中优先）
        scored.sort(key=lambda x: (-x[1], -x[2]))

        # 取 top_k，设置 rank
        result = []
        for i, (hit, _score, _src_count) in enumerate(scored[:top_k]):
            hit.rank = i + 1
            result.append(hit)

        return result

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _dedup_key(self, hit: RetrievalHit) -> str:
        """
        生成稳定、可靠的去重键。

        优先级从高到低：
        1. document_id + record_id — 文档内最可靠的业务主键组合
        2. document_id + row_number — Excel 表内行号（结构化查询常用）
        3. source + record_id — 来源内唯一（退而求其次）
        4. SHA256(content) — 内容级兜底（完整内容，稳定哈希）

        空字符串表示无可靠唯一键。
        """
        # Vector chunks and keyword records have different database IDs even
        # when they represent the same evidence. Keep different documents apart.
        if hit.document_id and hit.content and hit.source in (RetrievalSource.VECTOR, RetrievalSource.KEYWORD):
            digest = hashlib.sha256(hit.content.strip().encode("utf-8")).hexdigest()
            return f"doc:{hit.document_id}:content:{digest}"

        # 1. 文档 ID + 记录 ID
        if hit.document_id and hit.record_id:
            return f"doc:{hit.document_id}:rec:{hit.record_id}"

        # 2. 文档 ID + 行号（结构化查询场景下同一文档内唯一）
        if hit.document_id and hit.row_number is not None and hit.row_number > 0:
            return f"doc:{hit.document_id}:row:{hit.row_number}"

        # 3. 来源 + 记录 ID（同来源内至少唯一）
        if hit.record_id and hit.source:
            return f"src:{hit.source.value}:rec:{hit.record_id}"

        # 4. 完整内容 SHA256（稳定，不依赖 Python 进程）
        if hit.content:
            digest = hashlib.sha256(hit.content.encode("utf-8")).hexdigest()
            return f"content:sha256:{digest}"

        return ""
