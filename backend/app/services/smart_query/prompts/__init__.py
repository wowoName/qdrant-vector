"""
Prompt 加载器。

集中管理所有 LLM system prompt，从 prompts/ 目录的 .txt 文件加载，支持懒加载和缓存。

使用方式：
    from app.services.smart_query.prompts import get_prompt
    prompt = get_prompt("synthesis_system")
"""

from __future__ import annotations

from pathlib import Path

# prompts 目录（即本文件所在目录）
PROMPTS_DIR = Path(__file__).parent

# 缓存已加载的 prompt
_prompt_cache: dict[str, str] = {}


def list_prompts() -> list[str]:
    """列出所有可用的 prompt 名称"""
    names = []
    for f in PROMPTS_DIR.glob("*.txt"):
        names.append(f.stem)
    return sorted(names)


def get_prompt(name: str) -> str:
    """
    获取指定名称的 prompt 文本。

    Args:
        name: prompt 名称（对应 prompts/{name}.txt）

    Returns:
        prompt 文本内容

    Raises:
        FileNotFoundError: 找不到对应名称的 prompt 文件
    """
    if name in _prompt_cache:
        return _prompt_cache[name]

    prompt_path = PROMPTS_DIR / f"{name}.txt"
    if not prompt_path.exists():
        raise FileNotFoundError(
            f"Prompt '{name}' not found. Available: {list_prompts()}"
        )

    with open(prompt_path, "r", encoding="utf-8") as f:
        content = f.read()

    _prompt_cache[name] = content
    return content


def reload_prompt(name: str) -> str:
    """
    强制重新加载 prompt（清除缓存后重新读取）。

    用于运行时动态调整 prompt 的场景。
    """
    _prompt_cache.pop(name, None)
    return get_prompt(name)


__all__ = [
    "get_prompt",
    "list_prompts",
    "reload_prompt",
    "PROMPTS_DIR",
]
