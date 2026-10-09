"""
向量检索器（语义模糊查找）

基于 Qdrant 向量数据库做语义相似度检索。
返回统一的 RetrievalHit 列表。
"""

import time
from typing import List, Optional

from .base_retriever import BaseRetriever
from .retrieval_agent import RetrievalAgent
from .types import (
    QueryPlan,
    RetrievalHit,
    RetrievalSource,
    TableSchema,
)
from app.core.logging import get_logger
from app.services.vector_service import VectorService

logger = get_logger(__name__)


class VectorRetriever(BaseRetriever):
    """
    向量检索器

    用于语义查询、模糊查找、概念匹配等场景。
    基于 Qdrant 向量相似度搜索。
    """

    def __init__(
        self,
        vector_service: Optional[VectorService] = None,
        retrieval_agent: Optional[RetrievalAgent] = None,
        db=None,
    ):
        if retrieval_agent:
            self.retrieval_agent = retrieval_agent
        elif vector_service:
            self.retrieval_agent = RetrievalAgent(vector_service=vector_service)
        elif db is not None:
            self.retrieval_agent = RetrievalAgent(db=db)
        else:
            raise ValueError("必须提供 vector_service / retrieval_agent / db 之一")

    def is_applicable(self, plan: QueryPlan) -> bool:
        """
        判断是否适用向量检索

        适用场景：
        - SEMANTIC 意图（纯语义）
        - HYBRID 意图（混合）
        - needs_vector 为 True
        - 即使是 STRUCTURED 意图，也跑向量检索做补充（多路并行）
        """
        return True  # 多路并行架构下，所有查询都走向量检索做补充

    async def retrieve(
        self,
        query: str,
        plan: QueryPlan,
        schema: TableSchema,
        top_k: int = 20,
        collection_id: str = "",
    ) -> List[RetrievalHit]:
        """执行向量检索

        Args:
            query: 查询文本
            plan: 查询计划
            schema: 表结构
            top_k: 返回数量
            collection_id: Qdrant 集合 ID（知识库 ID），优先使用传入值，
                           回退到 schema.document_id（兼容纯向量模式）
        """
        start = time.time()
        hits: List[RetrievalHit] = []

        # 确定 Qdrant 集合 ID：优先用传入的 collection_id，
        # 回退到 schema.document_id（_vector_only_path 中会把 collection_id 填到这里）
        effective_collection = collection_id or schema.document_id or "default"

        try:
            result = await self.retrieval_agent.search(
                query=query,
                collection_id=effective_collection,
                top_k=top_k,
                document_id=schema.document_id if schema.document_id else None,
            )

            if result.success and result.results:
                for i, r in enumerate(result.results):
                    hit = RetrievalHit(
                        source=RetrievalSource.VECTOR,
                        score=r.get("score", 0.0),
                        rank=i + 1,
                        record_id=r.get("chunk_id", ""),
                        document_id=r.get("document_id", ""),
                        sheet_name=r.get("sheet_name", ""),
                        row_number=r.get("row_number", -1),
                        content=r.get("text", ""),
                        payload={
                            "chunk_index": r.get("chunk_index", 0),
                            "document_name": r.get("document_name", ""),
                            "file_type": r.get("file_type", ""),
                        },
                        metadata={
                            "page_num": r.get("page_num", 0),
                        },
                    )
                    hits.append(hit)

        except Exception as e:
            logger.error(f"Vector retrieval failed: {e}")

        elapsed_ms = (time.time() - start) * 1000
        for hit in hits:
            hit.metadata["execution_time_ms"] = elapsed_ms

        return hits
