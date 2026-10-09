"""
Chat API - 对话接口
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_user_id
from app.core.chat_database import get_chat_db
from app.models.schemas.chat import ChatRequest
from app.models.schemas.common import success, paginated
from app.services.chat_service import ChatService

router = APIRouter(tags=["Chat"])


# ========== 独立对话接口（不强制绑定知识库） ==========

@router.post("/chat", summary="对话问答（非流式，不传知识库则搜索全部）")
async def chat_ask_global(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
    user_id: str | None = Depends(get_user_id),
):
    """非流式对话，返回完整回答和引用来源
    collection_id 可选：
    - 传了：使用指定知识库 RAG 回答
    - 不传：搜索所有知识库进行 RAG 回答
    """
    service = ChatService(db, chat_db)
    result = await service.chat(req, user_id=user_id)
    return success(result.model_dump())


@router.post("/chat/stream", summary="对话问答（流式，不传知识库则搜索全部）")
async def chat_stream_global(
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
    user_id: str | None = Depends(get_user_id),
):
    """
    流式对话（SSE），逐块返回内容。
    collection_id 可选，不传则搜索所有知识库。

    返回格式（每行一个 JSON）：
    - metadata: {"type": "metadata", "session_id": "...", "references": [...]}
    - content:  {"type": "content", "content": "..."}
    - done:     {"type": "done", "answer": "...", "latency_ms": ..., "model": "..."}
    """
    stream_req = req.model_copy(update={"stream": True})
    service = ChatService(db, chat_db)

    async def event_generator():
        import json

        try:
            async for event in service.chat_stream(stream_req, user_id=user_id):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_event = {
                "type": "error",
                "message": f"服务异常：{str(e)}",
                "code": "internal_error",
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/chat/conversations", summary="全局会话列表（最近对话）")
async def list_all_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
    user_id: str | None = Depends(get_user_id),
):
    """列出所有会话（按更新时间倒序），用于左侧历史对话列表"""
    service = ChatService(db, chat_db)
    offset = (page - 1) * page_size
    convs, total = await service.list_all_conversations(
        limit=page_size, offset=offset, user_id=user_id
    )
    return paginated(
        items=[c.model_dump() for c in convs],
        total=total,
        page=page,
        page_size=page_size,
    )


# ========== 查询明细导出下载 ==========

@router.get("/chat/exports/{filename}", summary="下载查询明细导出文件")
async def download_query_export(filename: str):
    """下载智能问答导出的明细 Excel 文件（文件保留 24 小时）"""
    from urllib.parse import quote

    from app.services.smart_query.detail_export import resolve_export_file

    file_path = resolve_export_file(filename)
    if not file_path:
        raise HTTPException(status_code=404, detail="导出文件不存在或已过期")

    encoded_filename = quote(filename)
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        },
    )


# ========== 按知识库分组的对话接口（兼容旧版） ==========

@router.post("/collections/{collection_id}/chat", summary="对话问答（非流式）")
async def chat_ask(
    collection_id: str,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
):
    """非流式对话，返回完整回答和引用来源"""
    # 用路径参数覆盖请求体中的 collection_id
    bound_req = req.model_copy(update={"collection_id": collection_id})
    service = ChatService(db, chat_db)
    result = await service.chat(bound_req)
    return success(result.model_dump())


@router.post("/collections/{collection_id}/chat/stream", summary="对话问答（流式）")
async def chat_stream(
    collection_id: str,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
):
    """
    流式对话（SSE），逐块返回内容。

    返回格式（每行一个 JSON）：
    - metadata: {"type": "metadata", "session_id": "...", "references": [...]}
    - content:  {"type": "content", "content": "..."}
    - done:     {"type": "done", "answer": "...", "latency_ms": ..., "model": "..."}
    """
    bound_req = req.model_copy(update={"collection_id": collection_id, "stream": True})
    service = ChatService(db, chat_db)

    async def event_generator():
        import json

        try:
            async for event in service.chat_stream(bound_req):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as e:
            # 兜底：生成器内任何未被捕获的异常，都以 SSE error 事件返回，
            # 避免异常冒泡到 FastAPI 层导致 "response already started" 错误。
            error_event = {
                "type": "error",
                "message": f"服务异常：{str(e)}",
                "code": "internal_error",
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/collections/{collection_id}/conversations", summary="会话列表")
async def list_conversations(
    collection_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
):
    """列出指定集合下的所有会话（按更新时间倒序）"""
    service = ChatService(db, chat_db)
    convs = await service.list_conversations(collection_id, limit=limit, offset=offset)
    return success([c.model_dump() for c in convs])


@router.get("/chat/sessions/{session_id}", summary="会话详情")
async def get_conversation(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
):
    """获取会话详情（包含完整消息列表）"""
    service = ChatService(db, chat_db)
    conv = await service.get_conversation(session_id)
    if not conv:
        return success(None, message="会话不存在")
    return success(conv.model_dump())


@router.delete("/chat/sessions/{session_id}", summary="删除会话")
async def delete_conversation(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
):
    """删除（归档）指定会话，同时清理 Redis 缓存"""
    service = ChatService(db, chat_db)
    deleted = await service.delete_conversation(session_id)
    return success({"deleted": deleted})


# 保留原接口路径，兼容旧版客户端
@router.delete("/chat/sessions/{session_id}/clear", summary="清除会话历史（兼容旧版）")
async def clear_chat_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    chat_db: AsyncSession = Depends(get_chat_db),
):
    """清除指定会话的历史消息（同删除会话，保留兼容）"""
    service = ChatService(db, chat_db)
    cleared = await service.delete_conversation(session_id)
    return success({"cleared": cleared})
