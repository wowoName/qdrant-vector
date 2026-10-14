"""
LLM Provider 工厂

根据 provider 名称创建对应的 LLM 实例。每个 provider 有独立的实现类，
可以自定义请求格式、认证方式、响应解析等。

支持的 Provider：
- ark: 火山方舟（字节跳动/豆包）
- deepseek: DeepSeek
- qwen: 阿里通义千问（DashScope 兼容模式）
- ollama: Ollama 本地
- openai: OpenAI 官方
"""

import logging

from app.core.config import settings

from .ark import ArkLLM
from .base import BaseLLM, ChatMessage, ChatResponse
from .deepseek import DeepSeekLLM
from .ollama import OllamaLLM
from .openai import OpenAILLM
from .openai_compatible import OpenAICompatibleLLM
from .qwen import QwenLLM

logger = logging.getLogger(__name__)

# 已创建的 LLM 实例缓存（按 provider+model 维度）
_llm_cache: dict[str, BaseLLM] = {}


def _cache_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


def create_llm(
    provider: str | None = None,
    model: str | None = None,
    force_new: bool = False,
) -> BaseLLM:
    """
    创建 LLM 实例

    Args:
        provider: 提供商，不指定则使用配置中的 llm_provider
        model: 模型名/endpoint，不指定则使用配置中对应 provider 的默认模型
        force_new: 是否强制新建（跳过缓存）

    Returns:
        LLM 实例
    """
    provider = (provider or settings.llm_provider).strip().lower()
    model = (model or _get_default_model(provider)).strip()

    key = _cache_key(provider, model)
    if not force_new and key in _llm_cache:
        return _llm_cache[key]

    llm = _create_llm_internal(provider, model)
    if not force_new:
        _llm_cache[key] = llm
    logger.info(f"LLM instance created: provider={llm.provider_name}, model={llm.model_name}")
    return llm


def _get_default_model(provider: str) -> str:
    """获取指定 provider 的默认模型

    优先级：
    1. 各 provider 的专属配置（如 ollama_model）
    2. 全局 llm_model（仅当 provider 与全局 llm_provider 一致时）
    3. 内置默认值
    """
    provider_lower = provider.lower()

    # 有专属配置的 provider 优先使用专属配置
    dedicated = {
        "ollama": (settings.ollama_model or "").strip(),
    }
    if provider_lower in dedicated and dedicated[provider_lower]:
        return dedicated[provider_lower]

    # 全局 llm_model 兜底（仅当 provider 与全局一致时才使用，避免跨 provider 误用模型名）
    if settings.llm_model and settings.llm_provider.lower() == provider_lower:
        return settings.llm_model.strip()

    # 各 provider 的内置默认值
    defaults = {
        "ark": "",
        "deepseek": "deepseek-chat",
        "qwen": "qwen-plus",
        "ollama": "qwen2.5:7b",
        "openai": "gpt-4o-mini",
    }
    return defaults.get(provider_lower, "").strip()


def _create_llm_internal(provider: str, model: str) -> BaseLLM:
    """根据 provider 创建对应的 LLM 实例"""
    provider_lower = provider.lower()

    if provider_lower == "ark":
        return _create_ark_llm(model)
    elif provider_lower == "deepseek":
        return _create_deepseek_llm(model)
    elif provider_lower == "qwen":
        return _create_qwen_llm(model)
    elif provider_lower == "ollama":
        return _create_ollama_llm(model)
    elif provider_lower == "openai":
        return _create_openai_llm(model)
    else:
        # 未知 provider，当作通用 OpenAI 兼容处理
        logger.warning(f"未知 LLM provider: {provider}，使用通用 OpenAI 兼容模式")
        return OpenAICompatibleLLM(
            provider=provider_lower,
            model=model,
            api_key=settings.deepseek_api_key,
            base_url=settings.deepseek_base_url,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout=settings.llm_timeout,
        )


def _create_ark_llm(model: str) -> ArkLLM:
    """创建火山方舟 LLM 实例"""
    actual_model = (model or "").strip()
    if not actual_model:
        raise ValueError(
            "火山方舟模型未配置，请在 LLM_PROVIDER=ark 时设置 LLM_MODEL，或设置对应场景的 *_LLM_MODEL "
            "(需使用控制台的 Model ID 或 Endpoint ID，如 doubao-seed-2-1-pro-260628 / ep-xxxxx)"
        )

    api_key = (settings.ark_api_key or "").strip()
    if not api_key:
        raise ValueError("火山方舟 API Key 未配置，请设置 ARK_API_KEY 环境变量")

    base_url = (settings.ark_base_url or "").strip()
    if not base_url:
        base_url = "https://ark.cn-beijing.volces.com/api/v3"

    return ArkLLM(
        model=actual_model,
        api_key=api_key,
        base_url=base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
    )


def _create_deepseek_llm(model: str) -> DeepSeekLLM:
    """创建 DeepSeek LLM 实例"""
    actual_model = (model or "deepseek-chat").strip()
    api_key = (settings.deepseek_api_key or "").strip()
    if not api_key:
        raise ValueError("DeepSeek API Key 未配置，请设置 DEEPSEEK_API_KEY 环境变量")

    return DeepSeekLLM(
        model=actual_model,
        api_key=api_key,
        base_url=settings.deepseek_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
    )


def _create_qwen_llm(model: str) -> QwenLLM:
    """创建通义千问 LLM 实例"""
    actual_model = (model or "qwen-plus").strip()
    api_key = (settings.dashscope_api_key or "").strip()
    if not api_key:
        raise ValueError("DashScope API Key 未配置，请设置 DASHSCOPE_API_KEY 环境变量")

    return QwenLLM(
        model=actual_model,
        api_key=api_key,
        base_url=settings.qwen_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
    )


def _create_ollama_llm(model: str) -> OllamaLLM:
    """创建 Ollama LLM 实例（本地）"""
    actual_model = (model or settings.ollama_model or "qwen2.5:7b").strip()
    return OllamaLLM(
        model=actual_model,
        base_url=settings.ollama_base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
    )


def _create_openai_llm(model: str) -> OpenAILLM:
    """创建 OpenAI LLM 实例"""
    actual_model = (model or "gpt-4o-mini").strip()
    api_key = getattr(settings, "openai_api_key", None)
    api_key = api_key.strip() if api_key else None
    base_url = getattr(settings, "openai_base_url", "https://api.openai.com/v1")
    base_url = base_url.strip() if base_url else "https://api.openai.com/v1"

    return OpenAILLM(
        model=actual_model,
        api_key=api_key,
        base_url=base_url,
        temperature=settings.llm_temperature,
        max_tokens=settings.llm_max_tokens,
        timeout=settings.llm_timeout,
    )


async def test_llm_connection(
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """测试 LLM 连接是否正常

    Returns:
        {"ok": bool, "provider": str, "model": str, "error": str|None, "response": str|None}
    """
    try:
        llm = create_llm(provider=provider, model=model, force_new=True)
        resp = await llm.simple_chat(
            "请只回复一个词：OK",
            system_prompt="你是一个简洁的助手",
            temperature=0.0,
            max_tokens=128,
        )
        return {
            "ok": True,
            "provider": llm.provider_name,
            "model": llm.model_name,
            "response": resp.strip(),
            "error": None,
        }
    except Exception as e:
        provider_name = (provider or settings.llm_provider).strip().lower()
        model_name = (model or "").strip()
        logger.error(f"LLM connection test failed for {provider_name}/{model_name}: {e}")
        return {
            "ok": False,
            "provider": provider_name,
            "model": model_name,
            "response": None,
            "error": str(e),
        }


# ---------- 按场景获取模型 ----------


def get_chat_llm() -> BaseLLM:
    """获取对话场景使用的 LLM"""
    return create_llm(
        provider=settings.chat_llm_provider or settings.llm_provider,
        model=settings.chat_llm_model or None,
    )


def get_rewrite_llm() -> BaseLLM:
    """获取查询改写场景使用的 LLM"""
    return create_llm(
        provider=settings.rewrite_llm_provider or settings.llm_provider,
        model=settings.rewrite_llm_model or None,
    )


def get_summary_llm() -> BaseLLM:
    """获取摘要场景使用的 LLM"""
    return create_llm(
        provider=settings.summary_llm_provider or settings.llm_provider,
        model=settings.summary_llm_model or None,
    )


__all__ = [
    "BaseLLM",
    "ChatMessage",
    "ChatResponse",
    "create_llm",
    "get_chat_llm",
    "get_rewrite_llm",
    "get_summary_llm",
    "test_llm_connection",
    "ArkLLM",
    "DeepSeekLLM",
    "QwenLLM",
    "OllamaLLM",
    "OpenAILLM",
]
