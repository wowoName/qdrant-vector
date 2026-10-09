"""
火山方舟（VolcEngine ARK）LLM 实现

基于 OpenAI 兼容接口，特殊点：
- 模型必须是 Model ID 或 Endpoint ID（如 doubao-seed-2-1-pro-260628 / ep-xxxxx）
  不能是展示名（如 doubao-seed-2.1-turbo）
- 支持 thinking / reasoning 模式（reasoning_content 字段）
- 错误码格式：{error: {code: "InvalidEndpointOrModel.NotFound", message: "..."}}
"""

import logging
from collections.abc import AsyncGenerator
from typing import Any

from .base import ChatMessage, ChatResponse
from .openai_compatible import OpenAICompatibleLLM

logger = logging.getLogger(__name__)


class ArkLLM(OpenAICompatibleLLM):
    """火山方舟 LLM 实现

    注意：model 参数必须是火山方舟控制台中的真实 Model ID 或 Endpoint ID：
    - Model ID：形如 doubao-seed-2-1-pro-260628、doubao-pro-32k-240515
    - Endpoint ID：形如 ep-xxxxxxxxxxxxxxxx
    不能使用展示名（如 "doubao-seed-2.1-turbo"）。
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://ark.cn-beijing.volces.com/api/v3",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        enable_thinking: bool = False,
        **kwargs: Any,
    ):
        model = model.strip()
        super().__init__(
            provider="ark",
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            api_path="/chat/completions",
            auth_header="Authorization",
            auth_prefix="Bearer ",
        )
        self._enable_thinking = enable_thinking

    def _build_body(
        self,
        messages: list[ChatMessage],
        temperature: float | None,
        max_tokens: int | None,
        stream: bool = False,
        **kwargs: Any,
    ) -> dict:
        body = super()._build_body(messages, temperature, max_tokens, stream, **kwargs)
        # 方舟支持 thinking 模式（思考过程）
        if self._enable_thinking and "thinking" not in body:
            body["thinking"] = {"type": "enabled"}
        return body

    def _parse_choice_content(self, choice: dict) -> str:
        """从非流式响应的 choice 中提取最终内容。"""
        message = choice.get("message", {})
        return message.get("content", "") or ""

    def _parse_choice_reasoning(self, choice: dict) -> str:
        """从非流式响应的 choice 中提取思考过程。"""
        message = choice.get("message", {})
        return message.get("reasoning_content", "") or ""

    def _parse_delta_content(self, delta: dict) -> str:
        """从流式响应的 delta 中提取最终内容。"""
        return delta.get("content", "") or ""

    def _parse_delta_reasoning(self, delta: dict) -> str:
        """从流式响应的 delta 中提取思考过程。"""
        return delta.get("reasoning_content", "") or ""

    async def chat(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> ChatResponse:
        try:
            return await super().chat(messages, temperature, max_tokens, **kwargs)
        except RuntimeError as e:
            msg = str(e)
            if "InvalidEndpointOrModel.NotFound" in msg:
                raise RuntimeError(
                    f"火山方舟模型不存在或无权限：{self._model}。"
                    f"请检查 ARK_MODEL_ENDPOINT 配置，"
                    f"需使用控制台的 Model ID（如 doubao-seed-2-1-pro-260628）"
                    f"或 Endpoint ID（ep-xxxxx），不能使用展示名。"
                ) from e
            raise

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Any, None]:
        try:
            async for chunk in super().chat_stream(messages, temperature, max_tokens, **kwargs):
                yield chunk
        except RuntimeError as e:
            msg = str(e)
            if "InvalidEndpointOrModel.NotFound" in msg:
                raise RuntimeError(
                    f"火山方舟模型不存在或无权限：{self._model}。"
                    f"请检查 ARK_MODEL_ENDPOINT 配置，"
                    f"需使用控制台的 Model ID（如 doubao-seed-2-1-pro-260628）"
                    f"或 Endpoint ID（ep-xxxxx），不能使用展示名。"
                ) from e
            raise
