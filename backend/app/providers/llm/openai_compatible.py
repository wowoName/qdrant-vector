"""
OpenAI 兼容 LLM 基础实现

所有遵循 OpenAI Chat Completions API 规范的 provider 都可以继承此类，
按需覆写 _build_url / _build_headers / _build_body / _parse_choice 等方法。
"""

import httpx
from typing import List, Optional, AsyncGenerator, Any, Dict
import json

from .base import BaseLLM, ChatMessage, ChatResponse


class OpenAICompatibleLLM(BaseLLM):
    """OpenAI 兼容 LLM 基础实现"""

    def __init__(
        self,
        provider: str,
        model: str,
        api_key: Optional[str] = None,
        base_url: str = "https://api.openai.com/v1",
        temperature: float = 0.7,
        max_tokens: int = 2048,
        timeout: int = 120,
        api_path: str = "/chat/completions",
        auth_header: str = "Authorization",
        auth_prefix: str = "Bearer ",
        extra_headers: Optional[dict] = None,
        extra_body: Optional[dict] = None,
    ):
        self._provider = provider
        self._model = model.strip() if model else model
        self._api_key = api_key.strip() if api_key else api_key
        self._base_url = base_url.strip().rstrip("/")
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._timeout = timeout
        self._api_path = api_path
        self._auth_header = auth_header
        self._auth_prefix = auth_prefix
        self._extra_headers = extra_headers or {}
        self._extra_body = extra_body or {}

    # ---------- 基础属性 ----------

    @property
    def provider_name(self) -> str:
        return self._provider

    @property
    def model_name(self) -> str:
        return self._model

    # ---------- 可被子类覆写的构建方法 ----------

    def _build_url(self, stream: bool = False) -> str:
        """构造请求 URL。子类可覆写以支持不同路径。"""
        path = self._api_path if self._api_path.startswith("/") else f"/{self._api_path}"
        return f"{self._base_url}{path}"

    def _build_headers(self) -> dict:
        """构造请求头。子类可覆写以支持不同认证方式。"""
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers[self._auth_header] = f"{self._auth_prefix}{self._api_key}"
        headers.update(self._extra_headers)
        return headers

    def _build_body(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float],
        max_tokens: Optional[int],
        stream: bool = False,
        **kwargs: Any,
    ) -> dict:
        """构造请求体。子类可覆写以支持不同字段。"""
        body: dict = {
            "model": self._model,
            "messages": [m.to_dict() for m in messages],
            "stream": stream,
        }
        body["temperature"] = temperature if temperature is not None else self._temperature
        body["max_tokens"] = max_tokens if max_tokens is not None else self._max_tokens

        # provider 级别的默认额外参数
        for k, v in self._extra_body.items():
            if k not in body:
                body[k] = v
        # 调用时传入的额外参数覆盖默认值
        for k, v in kwargs.items():
            if k not in body:
                body[k] = v
        return body

    def _parse_choice_content(self, choice: dict) -> str:
        """从非流式响应的 choice 中提取最终内容。子类可覆写。"""
        message = choice.get("message", {})
        return message.get("content", "") or ""

    def _parse_choice_reasoning(self, choice: dict) -> str:
        """从非流式响应的 choice 中提取思考过程。子类可覆写。"""
        return ""

    def _parse_delta_content(self, delta: dict) -> str:
        """从流式响应的 delta 中提取最终内容。子类可覆写。"""
        return delta.get("content", "") or ""

    def _parse_delta_reasoning(self, delta: dict) -> str:
        """从流式响应的 delta 中提取思考过程。子类可覆写。"""
        return ""

    def _parse_usage(self, data: dict) -> dict:
        """从响应中提取 usage。子类可覆写。"""
        return data.get("usage", {}) or {}

    def _parse_model(self, data: dict) -> str:
        """从响应中提取模型名。子类可覆写。"""
        return data.get("model", self._model)

    # ---------- 错误解析 ----------

    @staticmethod
    def _parse_error_body(text: str) -> str:
        """从错误响应体文本中解析可读的错误信息。"""
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                if "error" in data:
                    err = data["error"]
                    if isinstance(err, dict):
                        msg = err.get("message", str(err))
                        code = err.get("code", "")
                        return f"{msg} (code={code})" if code else msg
                    return str(err)
                if "message" in data:
                    return str(data["message"])
            return str(data)
        except Exception:
            return text[:500]

    def _extract_error_message(self, resp: httpx.Response) -> str:
        """非流式场景的错误提取。"""
        try:
            return self._parse_error_body(resp.text)
        except Exception:
            return f"HTTP {resp.status_code}"

    async def _extract_error_message_async(self, resp: httpx.Response) -> str:
        """流式场景的错误提取（需要先 aread）。"""
        try:
            await resp.aread()
            return self._parse_error_body(resp.text)
        except Exception:
            return f"HTTP {resp.status_code}"

    # ---------- 核心方法 ----------

    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        url = self._build_url(stream=False)
        body = self._build_body(messages, temperature, max_tokens, stream=False, **kwargs)
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(url, json=body, headers=headers)
            if resp.status_code >= 400:
                err_msg = self._extract_error_message(resp)
                raise RuntimeError(
                    f"LLM API 调用失败 (HTTP {resp.status_code}): {err_msg}"
                )
            data = resp.json()

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("LLM API 返回为空：没有 choices 字段")

        content = self._parse_choice_content(choices[0])
        reasoning = self._parse_choice_reasoning(choices[0])
        usage = self._parse_usage(data)
        model = self._parse_model(data)
        return ChatResponse(content=content, model=model, usage=usage, reasoning_content=reasoning)

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Dict[str, str], None]:
        url = self._build_url(stream=True)
        body = self._build_body(messages, temperature, max_tokens, stream=True, **kwargs)
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", url, json=body, headers=headers) as resp:
                if resp.status_code >= 400:
                    err_msg = await self._extract_error_message_async(resp)
                    raise RuntimeError(
                        f"LLM API 调用失败 (HTTP {resp.status_code}): {err_msg}"
                    )
                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    # SSE 格式：data: {...}
                    if line.startswith("data: "):
                        line = line[6:]
                    # 结束标记
                    if line == "[DONE]":
                        break
                    try:
                        data = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue

                    choices = data.get("choices", [])
                    if not choices:
                        continue
                    delta = choices[0].get("delta", {})
                    content = self._parse_delta_content(delta)
                    reasoning = self._parse_delta_reasoning(delta)
                    if reasoning:
                        yield {"type": "reasoning", "content": reasoning}
                    if content:
                        yield {"type": "content", "content": content}
