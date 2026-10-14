"""
Task API（SaaS：任务按登录用户隔离）
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user
from app.models.db.user import UserDB
from app.models.db.document import DocumentDB
from app.models.db.task import TaskDB
from app.models.schemas.task import TaskResponse
from app.models.schemas.common import success, paginated
from app.services.task_service import TaskService

router = APIRouter(tags=["Tasks"])


async def _task_responses(db: AsyncSession, tasks: list[TaskDB]) -> list[TaskResponse]:
    """Use saved filenames, with one batch lookup for historical tasks."""
    document_ids = {
        task.payload["document_id"]
        for task in tasks
        if task.payload and task.payload.get("document_id") and not task.payload.get("file_name")
    }
    names = {}
    if document_ids:
        result = await db.execute(
            select(DocumentDB.id, DocumentDB.name).where(DocumentDB.id.in_(document_ids))
        )
        names = dict(result.all())
    responses = []
    for task in tasks:
        payload = task.payload or {}
        response = TaskResponse.model_validate(task)
        response.file_name = payload.get("file_name") or names.get(payload.get("document_id"))
        responses.append(response)
    return responses


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
        items=[item.model_dump() for item in await _task_responses(db, items)],
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
    response = (await _task_responses(db, [task]))[0]
    return success(response.model_dump())


@router.post("/{task_id}/cancel", summary="取消任务")
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: UserDB = Depends(get_current_user),
):
    service = TaskService(db)
    await service.cancel_task(task_id, user_id=user.id)
    return success(message="Task cancelled")
