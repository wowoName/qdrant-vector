"""
检索器基类（多路并行检索的统一接口）

所有检索器（SQL / Vector / Keyword）都实现同一个接口，
返回统一的 RetrievalHit 列表，方便后续融合。
"""

from abc import ABC, abstractmethod
from typing import List

from .types import RetrievalHit, QueryPlan, TableSchema


class BaseRetriever(ABC):
    """
    检索器基类

    统一接口：retrieve(query, plan, schema) -> list[RetrievalHit]
    """

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        plan: QueryPlan,
        schema: TableSchema,
        top_k: int = 20,
    ) -> List[RetrievalHit]:
        """
        执行检索

        Args:
            query: 原始用户问题
            plan: 查询计划（RouterAgent 的输出）
            schema: 表结构信息
            top_k: 返回结果数量上限

        Returns:
            统一格式的命中列表
        """
        pass

    @abstractmethod
    def is_applicable(self, plan: QueryPlan) -> bool:
        """
        判断本检索器是否适用于该查询计划

        用于决定是否启动这一路检索。
        """
        pass
