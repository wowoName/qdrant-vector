"""
关键词检索器（字面匹配）

基于 PostgreSQL 的 LIKE 查询，做关键词全文检索。
作为 SQL（精确结构化）和 Vector（语义模糊）之间的补充。

适用场景：
- 专有名词、编号、人名等精确字面匹配
- 向量检索可能漏掉的字面完全匹配
- 简短关键词查询
"""

import time
from typing import List, Optional

from sqlalchemy import select, and_, or_, text as sa_text

from .base_retriever import BaseRetriever
from .types import (
    QueryPlan,
    RetrievalHit,
    RetrievalSource,
    TableSchema,
)
from app.core.logging import get_logger
from app.core.sql_compat import safe_rollback

logger = get_logger(__name__)


class KeywordRetriever(BaseRetriever):
    """
    关键词检索器

    基于 PostgreSQL LIKE 的字面匹配检索。
    从查询中提取关键词，在 vector_text / data_json 中搜索。
    """

    def __init__(self, db_session=None):
        self.db = db_session  # SQLAlchemy async session

    def is_applicable(self, plan: QueryPlan) -> bool:
        """所有查询都可以做关键词检索作为补充"""
        return True

    async def retrieve(
        self,
        query: str,
        plan: QueryPlan,
        schema: TableSchema,
        top_k: int = 20,
    ) -> List[RetrievalHit]:
        """执行关键词检索"""
        start = time.time()
        hits: List[RetrievalHit] = []

        if not self.db or not schema.document_id:
            return hits

        try:
            # 提取关键词
            keywords = self._extract_keywords(query, plan)
            if not keywords:
                return hits

            hits = await self._search_keywords(keywords, schema, top_k)

        except Exception as e:
            logger.error(f"Keyword retrieval failed: {e}")
            # 回滚失败事务，避免污染共用 session 导致其它检索路径级联失败
            await safe_rollback(self.db)

        elapsed_ms = (time.time() - start) * 1000
        for i, hit in enumerate(hits):
            hit.rank = i + 1
            hit.metadata["execution_time_ms"] = elapsed_ms

        return hits

    def _extract_keywords(self, query: str, plan: QueryPlan) -> List[str]:
        """
        从查询中提取关键词

        策略：
        1. 优先使用 plan 中解析出的实体名称
        2. 去掉常见的疑问词、量词
        3. 保留 2 字以上的有意义词语
        """
        keywords = []

        # 从实体中提取
        if plan.entities:
            for ent in plan.entities:
                if ent.name and len(ent.name) >= 2:
                    keywords.append(ent.name)

        # 从查询中提取（简单分词：去掉常见停用词）
        stop_words = {
            "多少", "几个", "什么", "怎么", "哪些", "哪里", "哪个",
            "有多少", "有哪些", "是什么", "怎么样", "为什么",
            "吗", "呢", "啊", "吧", "了", "的", "和", "与",
            "手下", "里面", "当中", "其中",
        }

        # 简单处理：按常见分隔符切分
        import re
        raw_words = re.split(r'[，。、；\s,.;!?！？]+', query)
        for w in raw_words:
            w = w.strip()
            if len(w) >= 2 and w not in stop_words:
                # 去掉包含疑问词的
                if not any(sw in w for sw in ["多少", "什么", "怎么", "哪些", "哪里", "哪个"]):
                    if w not in keywords:
                        keywords.append(w)

        # 限制关键词数量（最多 5 个）
        return keywords[:5]

    async def _search_keywords(
        self, keywords: List[str], schema: TableSchema, top_k: int
    ) -> List[RetrievalHit]:
        """在 PostgreSQL 中做 LIKE 关键词搜索"""
        from app.models.db.document_record import DocumentRecordDB

        hits: List[RetrievalHit] = []
        document_id = schema.document_id

        if not keywords:
            return hits

        # 构建 LIKE 条件（在 vector_text 中搜索，任一关键词命中即算）
        like_conditions = []
        for kw in keywords:
            like_conditions.append(DocumentRecordDB.vector_text.like(f"%{kw}%"))

        conditions = [
            DocumentRecordDB.document_id == document_id,
            or_(*like_conditions),
        ]

        # 计算匹配度（命中的关键词越多得分越高）
        stmt = (
            select(DocumentRecordDB)
            .where(and_(*conditions))
            .order_by(DocumentRecordDB.row_number)
            .limit(top_k * 2)  # 多取一些，后面按匹配度排序
        )

        result = await self.db.execute(stmt)
        records = list(result.scalars().all())

        # 按关键词匹配数量排序（简单打分）
        scored = []
        for rec in records:
            text = rec.vector_text or ""
            match_count = sum(1 for kw in keywords if kw in text)
            if match_count > 0:
                score = match_count / len(keywords)  # 0-1 范围
                scored.append((score, rec))

        # 按得分降序排序
        scored.sort(key=lambda x: x[0], reverse=True)

        for i, (score, rec) in enumerate(scored[:top_k]):
            hit = RetrievalHit(
                source=RetrievalSource.KEYWORD,
                score=score,
                record_id=rec.id,
                document_id=rec.document_id,
                sheet_name=rec.sheet_name,
                row_number=rec.row_number,
                content=rec.vector_text or "",
                payload=rec.data,
                metadata={
                    "matched_keywords": [kw for kw in keywords if kw in (rec.vector_text or "")],
                    "keyword_match_count": int(score * len(keywords)),
                },
            )
            hits.append(hit)

        return hits
