"""
分片上传 API
"""

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user, get_visible_ids, require_writer
from app.models.db.user import UserDB
from app.models.schemas.document import (
    UploadInitRequest, UploadInitResponse,
    UploadStatusResponse, UploadCompleteRequest, UploadCompleteResponse,
)
from app.models.schemas.common import success
from app.services.upload_service import UploadService
from app.services.collection_service import CollectionService

router = APIRouter(tags=["Upload"])


@router.post("/init", summary="初始化分片上传")
async def init_upload(
    req: UploadInitRequest,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    # 校验目标 collection 在可见范围内
    collection_id = getattr(req, "collection_id", None)
    if collection_id:
        await CollectionService(db).get_by_id(collection_id, visible_ids=visible_ids)
    service = UploadService(db)
    result = await service.init_upload(req)
    return success(result.model_dump())


@router.put("/{upload_id}/{chunk_index}", summary="上传单个分片")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    # 校验该上传会话对应的 collection 在可见范围内
    service = UploadService(db)
    collection_id = await service.get_collection_id(upload_id)
    await CollectionService(db).get_by_id(collection_id, visible_ids=visible_ids)
    content = await file.read()
    result = await service.upload_chunk(upload_id, chunk_index, content)
    return success(result)


@router.get("/{upload_id}/status", summary="查询上传状态")
async def get_upload_status(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(get_current_user),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = UploadService(db)
    collection_id = await service.get_collection_id(upload_id)
    await CollectionService(db).get_by_id(collection_id, visible_ids=visible_ids)
    result = await service.get_status(upload_id)
    return success(result.model_dump())


@router.post("/{upload_id}/cancel", summary="取消上传")
async def cancel_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = UploadService(db)
    collection_id = await service.get_collection_id(upload_id)
    await CollectionService(db).get_by_id(collection_id, visible_ids=visible_ids)
    result = await service.cancel_upload(upload_id)
    return success(result)


@router.post("/{upload_id}/complete", summary="完成上传（合并分片）")
async def complete_upload(
    upload_id: str,
    req: UploadCompleteRequest,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = UploadService(db)
    collection_id = await service.get_collection_id(upload_id)
    # 校验该上传会话对应的 collection 在可见范围内
    await CollectionService(db).get_by_id(collection_id, visible_ids=visible_ids)
    result = await service.complete_upload(upload_id, req, user_id=user.id)
    return success(result.model_dump())
