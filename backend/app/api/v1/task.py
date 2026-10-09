"""
Task API（SaaS：任务按登录用户隔离）
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.db.user import UserDB
from app.models.schemas.task import TaskResponse
from app.models.schemas.common import success, paginated
from app.services.task_service import TaskService

router = APIRouter(tags=["Tasks"])


@router.get("", summary="获取任务列表")
async def list_tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    type: str = Query(None),
    status: str = Query(None),
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    service = TaskService(db)
    items, total = await service.list_tasks(
        page=page, page_size=page_size, task_type=type, status=status, user_id=user.id,
    )
    return paginated(
        items=[TaskResponse.model_validate(i).model_dump() for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/{task_id}", summary="获取任务详情")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    service = TaskService(db)
    task = await service.get_task(task_id, user_id=user.id)
    return success(TaskResponse.model_validate(task).model_dump())


@router.post("/{task_id}/cancel", summary="取消任务")
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    service = TaskService(db)
    await service.cancel_task(task_id, user_id=user.id)
    return success(message="Task cancelled")
