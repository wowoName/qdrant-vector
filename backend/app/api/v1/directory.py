"""
Directory API
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_visible_ids, require_writer
from app.models.db.user import UserDB
from app.models.schemas.directory import (
    DirectoryCreate, DirectoryUpdate, DirectoryMove,
    DirectoryResponse, DirectoryTreeNode,
)
from app.models.schemas.common import success
from app.services.directory_service import DirectoryService
from app.services.collection_service import CollectionService

router = APIRouter(tags=["Directories"])


async def _ensure_collection_access(db, collection_id: str, visible_ids: list[str]) -> None:
    await CollectionService(db).get_by_id(collection_id, visible_ids=visible_ids)


@router.get("/collections/{collection_id}/directories/tree", summary="获取目录树")
async def get_directory_tree(
    collection_id: str,
    db: AsyncSession = Depends(get_db),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    await _ensure_collection_access(db, collection_id, visible_ids)
    service = DirectoryService(db)
    tree = await service.get_tree(collection_id)
    return success([n.model_dump() for n in tree])


@router.post("/collections/{collection_id}/directories", summary="创建目录")
async def create_directory(
    collection_id: str,
    req: DirectoryCreate,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    await _ensure_collection_access(db, collection_id, visible_ids)
    service = DirectoryService(db)
    directory = await service.create(collection_id, req)
    return success(DirectoryResponse.model_validate(directory).model_dump())


@router.put("/directories/{directory_id}", summary="更新目录")
async def update_directory(
    directory_id: str,
    req: DirectoryUpdate,
    collection_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    await _ensure_collection_access(db, collection_id, visible_ids)
    service = DirectoryService(db)
    directory = await service.update(directory_id, collection_id, req)
    return success(DirectoryResponse.model_validate(directory).model_dump())


@router.post("/directories/{directory_id}/move", summary="移动目录")
async def move_directory(
    directory_id: str,
    req: DirectoryMove,
    collection_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    await _ensure_collection_access(db, collection_id, visible_ids)
    service = DirectoryService(db)
    directory = await service.move(directory_id, collection_id, req)
    return success(DirectoryResponse.model_validate(directory).model_dump())


@router.delete("/directories/{directory_id}", summary="删除目录")
async def delete_directory(
    directory_id: str,
    collection_id: str = Query(...),
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(require_writer),
    visible_ids: list[str] = Depends(get_visible_ids),
):
    await _ensure_collection_access(db, collection_id, visible_ids)
    service = DirectoryService(db)
    await service.delete(directory_id, collection_id)
    return success(message="Directory deleted")
