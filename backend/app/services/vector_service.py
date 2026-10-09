"""
向量检索服务
"""

import time
from typing import List

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.db.collection import CollectionDB
from app.models.schemas.search import SearchRequest, SearchResponse, SearchResult
from app.providers.embedding.factory import get_embedding
from app.repositories.qdrant_repo import get_qdrant
from app.services.collection_service import CollectionService

logger = get_logger(__name__)


class VectorService:
    """向量检索服务"""

    def __init__(self, db: AsyncSession):
        self.db = db
        self.qdrant = get_qdrant()

    async def _search_one_collection(
        self,
        collection_id: str,
        query_vector: List[float],
        req: SearchRequest,
    ) -> List[dict]:
        """在单个 collection 中搜索，返回原始 hit 列表"""
        # 维度一致性检查：如果查询向量维度与集合不匹配，跳过该集合
        # （可能是切换模型后旧集合还没重建）
        qdrant_dim = await self.qdrant.get_collection_vector_size(collection_id)
        if qdrant_dim is not None and qdrant_dim != len(query_vector):
            logger.warning(
                f"Skipping search on collection {collection_id}: "
                f"vector dimension mismatch (query={len(query_vector)}, collection={qdrant_dim})"
            )
            return []

        filters: dict[str, str] = {}
        if req.directory_id:
            filters["directory_id"] = req.directory_id
        if req.document_id:
            filters["document_id"] = req.document_id
        if req.file_type:
            filters["file_type"] = req.file_type

        results = await self.qdrant.search(
            collection_id=collection_id,
            query_vector=query_vector,
            top_k=req.top_k,
            score_threshold=req.score_threshold,
            filters=filters if filters else None,
        )
        # 给每个 hit 加上 collection_id
        for hit in results:
            hit["collection_id"] = collection_id
        return results

    def _build_results(self, hits: List[dict], top_k: int) -> List[SearchResult]:
        """将原始 hits 组装为 SearchResult 列表，并按分数排序取 top_k"""
        # 按分数降序排序
        hits_sorted = sorted(hits, key=lambda h: h.get("score", 0.0), reverse=True)

        search_results = []
        seen_parents = set()  # 用于父块去重

        for hit in hits_sorted:
            payload = hit.get("payload", {})
            parent_text = payload.get("parent_text", "")
            parent_index = payload.get("parent_index", -1)
            document_id = payload.get("document_id", "")
            collection_id = hit.get("collection_id", "")

            # 如果有父块，且同一个父块只返回一次（避免重复）
            if parent_text and parent_index >= 0:
                parent_key = f"{collection_id}_{document_id}_{parent_index}"
                if parent_key in seen_parents:
                    continue
                seen_parents.add(parent_key)

                # 用父块作为返回文本（更完整的上下文）
                display_text = parent_text
            else:
                display_text = payload.get("text", "")

            search_results.append(
                SearchResult(
                    chunk_id=payload.get("chunk_id", hit.get("id", "")),
                    collection_id=collection_id,
                    document_id=document_id,
                    document_name=payload.get("file_name", ""),
                    version_id=payload.get("version_id", ""),
                    chunk_index=payload.get("chunk_index", 0),
                    text=display_text,
                    score=hit.get("score", 0.0),
                    page_num=payload.get("page_num") if payload.get("page_num", -1) != -1 else None,
                    file_type=payload.get("file_type"),
                    payload=payload,
                )
            )

            if len(search_results) >= top_k:
                break

        return search_results

    async def search(self, collection_id: str, req: SearchRequest) -> SearchResponse:
        """语义检索。collection_id 为 'all' 或空时，搜索所有启用的知识库"""
        start_time = time.time()

        # 向量化查询
        embedding = await get_embedding()
        query_vector = (await embedding.embed_texts([req.query]))[0]

        # 判断是否跨库搜索
        is_cross_collection = collection_id == "all" or not collection_id

        if is_cross_collection:
            # 查询所有启用的 collection
            result = await self.db.execute(
                select(CollectionDB).where(CollectionDB.is_active == True)
            )
            collections = result.scalars().all()

            all_hits: List[dict] = []
            for coll in collections:
                try:
                    hits = await self._search_one_collection(coll.id, query_vector, req)
                    all_hits.extend(hits)
                except Exception as e:
                    logger.warning(f"Search collection {coll.id} failed: {e}")
                    continue

            search_results = self._build_results(all_hits, req.top_k)
        else:
            # 校验 Collection
            coll_svc = CollectionService(self.db)
            await coll_svc.get_by_id(collection_id)

            hits = await self._search_one_collection(collection_id, query_vector, req)
            search_results = self._build_results(hits, req.top_k)

        latency_ms = (time.time() - start_time) * 1000

        return SearchResponse(
            results=search_results,
            total=len(search_results),
            query=req.query,
            latency_ms=round(latency_ms, 2),
        )
