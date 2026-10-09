"""
通义千问（阿里 DashScope）LLM 实现

使用 OpenAI 兼容模式：
- base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
- 认证: Authorization: Bearer {api_key}
"""

from .openai_compatible import OpenAICompatibleLLM


class QwenLLM(OpenAICompatibleLLM):
    """通义千问 LLM 实现"""

    def __init__(
        self,
        model: str = "qwen-plus",
        api_key: str | None = None,
        base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        **kwargs,
    ):
        super().__init__(
            provider="qwen",
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            api_path="/chat/completions",
        )
