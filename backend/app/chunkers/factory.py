"""
分块器工厂
"""

from app.core.config import settings, get_chunk_config, get_tabular_config

from .base import BaseChunker
from .fixed_size_chunker import FixedSizeChunker
from .parent_child_chunker import ParentChildChunker
from .recursive_chunker import RecursiveChunker
from .semantic_chunker import SemanticChunker
from .tabular_chunker import TabularChunker


# 表格类文件类型（自动使用 tabular 切片器）
TABULAR_FILE_TYPES = {"xlsx", "xls", "excel", "csv"}


def get_chunker(strategy: str | None = None, file_type: str | None = None) -> BaseChunker:
    """
    获取分块器实例

    支持的策略:
    - fixed: 固定大小分块
    - recursive / recursive_character: 递归字符分块（默认）
    - semantic: 语义感知分块（基于 embedding 相似度）
    - parent_child / hybrid: 父子块分块（推荐，检索质量最好）
    - tabular / table / excel: 表格专用分块器（Excel/CSV 等结构化数据）
    - auto: 自动选择（表格文件自动用 tabular，其余用 recursive）

    Args:
        strategy: 切片策略
        file_type: 文件类型（用于 auto 模式自动选择切片器）
    """
    cfg = get_chunk_config(file_type)
    strategy = (strategy or cfg["strategy"]).lower()
    if strategy == "auto":
        strategy = "tabular" if file_type and file_type.lower() in TABULAR_FILE_TYPES else "recursive"
    params = {"chunk_size": cfg["size"], "chunk_overlap": cfg["overlap"], "min_size": cfg["min_size"]}
    if strategy in ("tabular", "table", "excel", "row"):
        table_cfg = get_tabular_config(file_type)
        return TabularChunker(
            mode=table_cfg["mode"], rows_per_chunk=table_cfg["rows_per_chunk"],
            columns_per_group=table_cfg["columns_per_group"], include_headers=table_cfg["include_headers"],
            chunk_size=cfg["size"], chunk_overlap=cfg["overlap"],
        )
    if strategy == "fixed":
        return FixedSizeChunker(**params)
    if strategy == "semantic":
        return SemanticChunker(**params)
    if strategy in ("parent_child", "hybrid", "parent-child"):
        return ParentChildChunker(**params)
    return RecursiveChunker(**params)


def get_chunker_for_file(file_type: str, strategy: str | None = None) -> BaseChunker:
    """
    根据文件类型智能选择分块器。

    - Excel / CSV 等表格文件：默认使用 TabularChunker
    - 其他文件：使用配置的策略或递归分块

    Args:
        file_type: 文件类型
        strategy: 可选的覆盖策略

    Returns:
        合适的分块器实例
    """
    # 如果用户显式指定了策略且非 auto，就用指定的
    if strategy and strategy.lower() not in ("auto", ""):
        return get_chunker(strategy=strategy, file_type=file_type)

    # 自动选择
    if file_type and file_type.lower() in TABULAR_FILE_TYPES:
        return get_chunker(strategy="tabular", file_type=file_type)

    return get_chunker(strategy=strategy, file_type=file_type)
