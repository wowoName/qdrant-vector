"""
LLM Provider 基类
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional, AsyncGenerator


class ChatMessage:
    """聊天消息"""

    def __init__(self, role: str, content: str):
        self.role = role
        self.content = content

    def to_dict(self) -> Dict[str, str]:
        return {"role": self.role, "content": self.content}


class ChatResponse:
    """聊天响应"""

    def __init__(
        self,
        content: str,
        model: str,
        usage: Optional[Dict[str, int]] = None,
        reasoning_content: Optional[str] = None,
    ):
        self.content = content
        self.model = model
        self.usage = usage or {}
        self.reasoning_content = reasoning_content or ""

    def __repr__(self) -> str:
        return f"ChatResponse(model={self.model}, content_len={len(self.content)}, reasoning_len={len(self.reasoning_content)})"


class BaseLLM(ABC):
    """LLM 模型抽象"""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """提供商名称"""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """模型名称"""
        pass

    @abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> ChatResponse:
        """同步聊天（非流式）"""
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """流式聊天，yield 结构为 {"type": "content"|"reasoning", "content": "..."}"""
        pass

    async def simple_chat(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs: Any,
    ) -> str:
        """简单单轮对话便捷方法"""
        messages: List[ChatMessage] = []
        if system_prompt:
            messages.append(ChatMessage("system", system_prompt))
        messages.append(ChatMessage("user", prompt))
        resp = await self.chat(messages, temperature=temperature, max_tokens=max_tokens, **kwargs)
        return resp.content
