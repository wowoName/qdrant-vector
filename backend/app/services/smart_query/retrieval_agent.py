"""
RetrievalAgent - 向量检索代理

封装现有的向量检索能力，供 SmartQuery 编排器调用。

职责：
1. 调用 VectorService 执行向量检索
2. 对检索结果进行后处理（去重、过滤、格式化）
3. 提供检索结果的质量评估（最高分、平均得分等）

设计原则：
- 薄封装，不重复造轮子
- 与现有 VectorService 完全兼容
- 提供统一的 RetrievalQueryResult 输出格式
"""

from __future__ import annotations

import time
from typing import Optional

from app.core.logging import get_logger
from app.models.schemas.search import SearchRequest
from app.services.vector_service import VectorService
from .types import RetrievalQueryResult

logger = get_logger(__name__)


class RetrievalAgent:
    """
    向量检索代理

    封装向量检索能力，为 SmartQuery 系统提供统一的检索接口。
    """

    def __init__(self, vector_service: Optional[VectorService] = None, db=None, visible_ids=None):
        self.visible_ids = visible_ids
        if vector_service:
            self.vector_service = vector_service
        elif db is not None:
            self.vector_service = VectorService(db)
        else:
            raise ValueError("必须提供 vector_service 或 db")

    async def search(
        self,
        query: str,
        collection_id: str,
        top_k: int = 10,
        score_threshold: float = 0.0,
        directory_id: Optional[str] = None,
        document_id: Optional[str] = None,
        file_type: Optional[str] = None,
    ) -> RetrievalQueryResult:
        """
        执行向量检索

        Args:
            query: 查询文本
            collection_id: 知识库ID
            top_k: 返回数量
            score_threshold: 相似度阈值
            directory_id: 目录过滤
            document_id: 文档过滤
            file_type: 文件类型过滤

        Returns:
            RetrievalQueryResult
        """
        start_time = time.time()

        try:
            search_req = SearchRequest(
                query=query,
                top_k=top_k,
                score_threshold=score_threshold,
                directory_id=directory_id,
                document_id=document_id,
                file_type=file_type,
            )

            search_response = await self.vector_service.search(
                collection_id, search_req, visible_ids=self.visible_ids
            )

            results = []
            max_score = 0.0

            for r in search_response.results:
                result_dict = {
                    "chunk_id": r.chunk_id,
                    "collection_id": r.collection_id,
                    "document_id": r.document_id,
                    "document_name": r.document_name,
                    "chunk_index": r.chunk_index,
                    "text": r.text,
                    "score": r.score,
                    "page_num": r.page_num,
                    "file_type": r.file_type,
                }
                results.append(result_dict)
                if r.score > max_score:
                    max_score = r.score

            return RetrievalQueryResult(
                success=True,
                results=results,
                total=len(results),
                max_score=max_score,
                execution_time_ms=(time.time() - start_time) * 1000,
            )

        except Exception as e:
            logger.error(f"Vector search failed: {e}")
            return RetrievalQueryResult(
                success=False,
                error=str(e),
                execution_time_ms=(time.time() - start_time) * 1000,
            )

    def build_context_text(self, result: RetrievalQueryResult, max_chunks: int = 5) -> str:
        """
        将检索结果格式化为上下文文本（用于LLM提示词）

        Args:
            result: 检索结果
            max_chunks: 最大块数

        Returns:
            格式化的上下文文本
        """
        if not result.success or not result.results:
            return "（无相关参考资料）"

        parts = []
        for i, r in enumerate(result.results[:max_chunks], 1):
            page_info = f"（第 {r.get('page_num', '')} 页）" if r.get("page_num") else ""
            doc_name = r.get("document_name", "未知文档")
            text = r.get("text", "")
            score = r.get("score", 0.0)
            parts.append(
                f"[{i}] 《{doc_name}》{page_info} (相似度: {score:.3f})\n{text}"
            )

        return "\n\n".join(parts)
