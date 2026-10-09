"""
Chat / LLM 相关 Schema
"""

from typing import Optional, List
from datetime import datetime
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="user/assistant/system")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    """对话请求"""
    query: str = Field(..., description="用户问题")
    collection_id: Optional[str] = Field(default=None, description="检索的 Collection ID（可选，不传则搜索全部知识库）")
    session_id: Optional[str] = Field(default=None, description="会话 ID")
    stream: bool = Field(default=False, description="是否流式输出")
    top_k: int = Field(default=5, ge=1, le=50, description="检索数量")
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    use_rewrite: bool = Field(default=True, description="是否改写问题")


class ChatReference(BaseModel):
    """引用来源"""
    chunk_id: str
    document_id: str
    document_name: str
    chunk_index: int
    text: str
    score: float
    page_num: Optional[int] = None
    collection_id: Optional[str] = None
    """所属知识库 ID（跨库搜索时返回）"""


class ChatResponse(BaseModel):
    """对话响应（非流式）"""
    session_id: str
    answer: str
    reasoning: str = ""
    """思考过程（推理内容），如果模型支持 thinking 模式"""
    references: List[ChatReference] = []
    latency_ms: float = 0.0
    model: str = ""
    is_kb_answer: bool = True
    """是否为知识库回答（False 表示向量库未命中，由大模型直接回答）"""


class ConversationSchema(BaseModel):
    """会话详情"""
    session_id: str
    collection_id: Optional[str] = None
    title: Optional[str] = None
    message_count: int = 0
    total_tokens: int = 0
    status: str = "active"
    created_at: datetime
    updated_at: datetime
    messages: List[ChatMessage] = []

    @classmethod
    def from_db(cls, db_obj) -> "ConversationSchema":
        return cls(
            session_id=db_obj.session_id,
            collection_id=db_obj.collection_id,
            title=db_obj.title,
            message_count=db_obj.message_count,
            total_tokens=db_obj.total_tokens,
            status=db_obj.status,
            created_at=db_obj.created_at,
            updated_at=db_obj.updated_at,
            messages=[],
        )
