"""
Document API
"""

import mimetypes
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, UploadFile, File, Query, Form, HTTPException
from fastapi.responses import StreamingResponse, FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import Optional

from app.api.deps import get_db
from app.models.schemas.document import (
    DocumentResponse, DocumentVersionResponse, ChunkResponse, SimpleUploadResponse,
)
from app.models.schemas.common import success, paginated
from app.services.document_service import DocumentService
from app.providers.storage.factory import get_storage
from app.core.exceptions import DocumentNotFoundError

router = APIRouter(tags=["Documents"])


@router.get("/collections/{collection_id}/documents", summary="获取文档列表")
async def list_documents(
    collection_id: str,
    directory_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None),
    file_type: str = Query(None),
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    items, total = await service.list_documents(
        collection_id=collection_id,
        directory_id=directory_id,
        page=page,
        page_size=page_size,
        keyword=keyword,
        file_type=file_type,
    )
    return paginated(
        items=[DocumentResponse.model_validate(i).model_dump() for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/collections/{collection_id}/documents/upload", summary="单文件上传")
async def upload_document(
    collection_id: str,
    file: UploadFile = File(...),
    directory_id: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    service = DocumentService(db)
    result = await service.upload_file(
        collection_id=collection_id,
        filename=file.filename or "unnamed",
        content=content,
        directory_id=directory_id,
    )
    return success(result.model_dump())


@router.get("/documents/{document_id}", summary="获取文档详情")
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    doc = await service.get_document(document_id)
    return success(DocumentResponse.model_validate(doc).model_dump())


@router.delete("/documents/{document_id}", summary="删除文档")
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    await service.delete_document(document_id)
    return success(message="Document deleted")


@router.get("/documents/{document_id}/versions", summary="获取版本列表")
async def get_document_versions(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    versions = await service.get_versions(document_id)
    return success([DocumentVersionResponse.model_validate(v).model_dump() for v in versions])


@router.get("/versions/{version_id}/chunks", summary="获取 Chunk 列表")
async def get_version_chunks(
    version_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    chunks, total = await service.get_chunks(version_id, page, page_size)
    return paginated(
        items=[ChunkResponse.model_validate(c).model_dump() for c in chunks],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("/documents/{document_id}/reindex", summary="重新索引文档")
async def reindex_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = DocumentService(db)
    task_id = await service.reindex(document_id)
    return success({"task_id": task_id})


@router.get("/documents/{document_id}/download", summary="下载文档文件")
@router.get("/documents/{document_id}/download/{filename}", summary="下载文档文件（带文件名）")
async def download_document(
    document_id: str,
    filename: Optional[str] = None,
    preview: bool = Query(False, description="是否以内联方式预览（在浏览器中打开）"),
    db: AsyncSession = Depends(get_db),
):
    """下载或预览文档文件"""
    service = DocumentService(db)
    try:
        doc = await service.get_document(document_id)
    except DocumentNotFoundError:
        raise HTTPException(status_code=404, detail="Document not found")

    version = await service.get_current_version(document_id)
    if not version:
        raise HTTPException(status_code=404, detail="No file version available")

    storage = get_storage()

    # 对于本地存储，直接返回文件响应
    local_path = storage.get_local_path(version.storage_path)
    if local_path:
        # 推断 MIME 类型
        media_type = mimetypes.guess_type(doc.name)[0] or "application/octet-stream"
        content_disposition = "inline" if preview else "attachment"
        filename = doc.name
        # 对非 ASCII 文件名进行编码
        encoded_filename = quote(filename)
        headers = {
            "Content-Disposition": f"{content_disposition}; filename*=UTF-8''{encoded_filename}",
        }
        return FileResponse(
            path=local_path,
            media_type=media_type,
            headers=headers,
            filename=filename if not preview else None,
        )

    # 非本地存储，流式返回
    data = await storage.download(version.storage_path)
    media_type = mimetypes.guess_type(doc.name)[0] or "application/octet-stream"
    content_disposition = "inline" if preview else "attachment"
    encoded_filename = quote(doc.name)
    headers = {
        "Content-Disposition": f"{content_disposition}; filename*=UTF-8''{encoded_filename}",
        "Content-Length": str(len(data)),
    }

    async def iter_bytes():
        yield data

    return StreamingResponse(
        iter_bytes(),
        media_type=media_type,
        headers=headers,
    )
