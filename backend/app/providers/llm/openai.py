"""
OpenAI LLM 实现
"""

from typing import Optional

from .openai_compatible import OpenAICompatibleLLM


class OpenAILLM(OpenAICompatibleLLM):
    """OpenAI LLM 实现"""

    def __init__(
        self,
        model: str = "gpt-4o-mini",
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        **kwargs,
    ):
        super().__init__(
            provider="openai",
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            api_path="/chat/completions",
        )
