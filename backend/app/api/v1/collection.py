"""
Collection API（SaaS：按登录用户/管理员可见范围隔离）
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_visible_ids, require_writer
from app.models.db.user import UserDB
from app.models.schemas.collection import (
    CollectionCreate, CollectionUpdate, CollectionResponse, CollectionStats,
)
from app.models.schemas.common import success, paginated
from app.services.collection_service import CollectionService

router = APIRouter()


@router.get("", summary="获取 Collection 列表")
async def list_collections(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    keyword: str = Query(None),
    db: AsyncSession = Depends(get_db),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = CollectionService(db)
    items, total = await service.list(
        page=page, page_size=page_size, keyword=keyword, visible_ids=visible_ids
    )
    return paginated(
        items=[CollectionResponse.model_validate(i).model_dump() for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.post("", summary="创建 Collection")
async def create_collection(
    req: CollectionCreate,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
):
    service = CollectionService(db)
    collection = await service.create(req, owner_user_id=user.id)
    return success(CollectionResponse.model_validate(collection).model_dump())


@router.get("/{collection_id}", summary="获取 Collection 详情")
async def get_collection(
    collection_id: str,
    db: AsyncSession = Depends(get_db),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = CollectionService(db)
    collection = await service.get_by_id(collection_id, visible_ids=visible_ids)
    return success(CollectionResponse.model_validate(collection).model_dump())


@router.put("/{collection_id}", summary="更新 Collection")
async def update_collection(
    collection_id: str,
    req: CollectionUpdate,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = CollectionService(db)
    collection = await service.update(collection_id, req, visible_ids=visible_ids)
    return success(CollectionResponse.model_validate(collection).model_dump())


@router.delete("/{collection_id}", summary="删除 Collection")
async def delete_collection(
    collection_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = CollectionService(db)
    await service.delete(collection_id, visible_ids=visible_ids)
    return success(message="Collection deleted")


@router.get("/{collection_id}/stats", summary="获取 Collection 统计")
async def get_collection_stats(
    collection_id: str,
    db: AsyncSession = Depends(get_db),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    service = CollectionService(db)
    stats = await service.get_stats(collection_id, visible_ids=visible_ids)
    return success(stats.model_dump())
