"""
SmartQuery 智能问数系统

多 Agent 协同架构，解决 Excel/CSV 等结构化数据查询不准确的问题。

架构：
    用户问题
       ↓
    RouterAgent（意图分类 + 实体解析 + 任务拆解）
       ↓
    ┌─────────┬──────────┬──────────┐
    ↓         ↓          ↓          ↓
 SQLAgent  Retrieval   Hybrid   DirectAnswer
 (SQL查询)  (向量检索)  (混合)    (直接回答)
    └─────────┴──────────┴──────────┘
                    ↓
            SynthesizerAgent（结果融合 + 答案生成）
                    ↓
                  最终回答

核心设计原则：
1. 结构化查询走 SQL（DuckDB 直查 Excel），语义查询走向量搜索
2. Qdrant 负责"找到相关数据"，DuckDB 负责"证明数据是多少"
3. 三层数据模型：原始层(文件) → 结构化层(DuckDB) → 语义层(Qdrant)
4. Excel 导入必须做 Forward Fill（合并单元格空值继承）
5. 组织层级关系显式建模，支持"手下/下属/负责"等隐含关系查询
"""

from .types import (
    QueryIntent,
    QueryOperation,
    QueryPlan,
    SQLQueryResult,
    RetrievalQueryResult,
    AgentContext,
    TableSchema,
    ColumnInfo,
    EntityInfo,
    FilterInfo,
)
from .router_agent import RouterAgent
from .sql_agent import SQLAgent
from .synthesizer_agent import SynthesizerAgent
from .schema_provider import SchemaProvider

# 以下模块依赖外部系统（数据库、Qdrant 等），使用懒加载避免导入时立即触发
_LAZY_MODULES = {
    "SmartQueryOrchestrator": ("orchestrator", "SmartQueryOrchestrator"),
    "RetrievalAgent": ("retrieval_agent", "RetrievalAgent"),
}


def __getattr__(name: str):
    """懒加载重依赖模块，避免导入 smart_query 时立即加载 DB/Qdrant 等依赖。"""
    if name in _LAZY_MODULES:
        module_name, attr_name = _LAZY_MODULES[name]
        import importlib
        mod = importlib.import_module(f".{module_name}", __name__)
        return getattr(mod, attr_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "SmartQueryOrchestrator",
    "RouterAgent",
    "SQLAgent",
    "RetrievalAgent",
    "SynthesizerAgent",
    "SchemaProvider",
    "QueryIntent",
    "QueryOperation",
    "QueryPlan",
    "SQLQueryResult",
    "RetrievalQueryResult",
    "AgentContext",
    "TableSchema",
    "ColumnInfo",
    "EntityInfo",
    "FilterInfo",
]
