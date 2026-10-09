"""
Ollama LLM 实现（本地运行）

Ollama 提供 OpenAI 兼容接口：http://localhost:11434/v1
"""

from .openai_compatible import OpenAICompatibleLLM


class OllamaLLM(OpenAICompatibleLLM):
    """Ollama LLM 实现"""

    def __init__(
        self,
        model: str = "qwen2.5:7b",
        base_url: str = "http://localhost:11434",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        **kwargs,
    ):
        base_url = base_url.strip().rstrip("/")
        super().__init__(
            provider="ollama",
            model=model,
            api_key="ollama",  # Ollama 不校验 key，但请求头需要有值
            base_url=f"{base_url}/v1",
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            api_path="/chat/completions",
        )
