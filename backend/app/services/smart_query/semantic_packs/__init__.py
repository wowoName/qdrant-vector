"""
业务语义包模块。

集中管理不同行业的业务语义配置，包括：
- 层级维度（hierarchy_levels）
- 列名同义词（column_aliases）
- 查询意图关键词（intent_keywords）

使用方式：
    from app.services.smart_query.semantic_packs import load_semantic_pack
    pack = load_semantic_pack("sales")  # 加载销售行业语义包
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

# 语义包目录
PACKS_DIR = Path(__file__).parent

# 缓存已加载的语义包
_pack_cache: dict[str, dict[str, Any]] = {}


def list_available_packs() -> list[str]:
    """列出所有可用的语义包名称"""
    packs = []
    for f in PACKS_DIR.glob("*.yaml"):
        packs.append(f.stem)
    return sorted(packs)


def load_semantic_pack(name: str = "sales") -> dict[str, Any]:
    """
    加载指定名称的业务语义包。

    Args:
        name: 语义包名称（对应 semantic_packs/{name}.yaml），默认 "sales"

    Returns:
        语义配置字典，包含 hierarchy_levels / column_aliases / intent_keywords

    Raises:
        FileNotFoundError: 找不到对应名称的语义包
    """
    if name in _pack_cache:
        return _pack_cache[name]

    pack_path = PACKS_DIR / f"{name}.yaml"
    if not pack_path.exists():
        # 兜底：如果指定的行业包不存在，回退到 generic
        fallback_path = PACKS_DIR / "generic.yaml"
        if fallback_path.exists() and name != "generic":
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(
                f"Semantic pack '{name}' not found, falling back to 'generic'"
            )
            pack_path = fallback_path
        else:
            raise FileNotFoundError(
                f"Semantic pack '{name}' not found. "
                f"Available: {list_available_packs()}"
            )

    with open(pack_path, "r", encoding="utf-8") as f:
        pack_data = yaml.safe_load(f) or {}

    # 规范化：确保所有必要的 key 都存在
    pack_data.setdefault("hierarchy_levels", {})
    pack_data.setdefault("column_aliases", {})
    pack_data.setdefault("intent_keywords", {})

    # 规范化 intent_keywords：确保四个子类型都存在
    intent = pack_data["intent_keywords"]
    intent.setdefault("count", [])
    intent.setdefault("list", [])
    intent.setdefault("group", [])
    intent.setdefault("top", [])

    _pack_cache[name] = pack_data
    return pack_data


def reload_semantic_pack(name: str) -> dict[str, Any]:
    """
    强制重新加载语义包（清除缓存后重新加载）。

    用于运行时动态更新配置的场景。
    """
    _pack_cache.pop(name, None)
    return load_semantic_pack(name)


__all__ = [
    "list_available_packs",
    "load_semantic_pack",
    "reload_semantic_pack",
    "PACKS_DIR",
]
