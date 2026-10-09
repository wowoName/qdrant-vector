"""
分片上传 API
"""

from fastapi import APIRouter, Depends, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db
from app.models.schemas.document import (
    UploadInitRequest, UploadInitResponse,
    UploadStatusResponse, UploadCompleteRequest, UploadCompleteResponse,
)
from app.models.schemas.common import success
from app.services.upload_service import UploadService

router = APIRouter(tags=["Upload"])


@router.post("/init", summary="初始化分片上传")
async def init_upload(
    req: UploadInitRequest,
    db: AsyncSession = Depends(get_db),
):
    service = UploadService(db)
    result = await service.init_upload(req)
    return success(result.model_dump())


@router.put("/{upload_id}/{chunk_index}", summary="上传单个分片")
async def upload_chunk(
    upload_id: str,
    chunk_index: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    content = await file.read()
    service = UploadService(db)
    result = await service.upload_chunk(upload_id, chunk_index, content)
    return success(result)


@router.get("/{upload_id}/status", summary="查询上传状态")
async def get_upload_status(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = UploadService(db)
    result = await service.get_status(upload_id)
    return success(result.model_dump())


@router.post("/{upload_id}/cancel", summary="取消上传")
async def cancel_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
):
    service = UploadService(db)
    result = await service.cancel_upload(upload_id)
    return success(result)


@router.post("/{upload_id}/complete", summary="完成上传（合并分片）")
async def complete_upload(
    upload_id: str,
    req: UploadCompleteRequest,
    db: AsyncSession = Depends(get_db),
):
    service = UploadService(db)
    result = await service.complete_upload(upload_id, req)
    return success(result.model_dump())
