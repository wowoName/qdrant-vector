"""
任务服务 - 轻量异步任务管理
"""

import asyncio
import time
import traceback
from collections.abc import Callable
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.db.task import TaskDB
from app.utils.hash_utils import generate_id

logger = get_logger(__name__)

# 全局任务注册表：task_type -> 处理函数
_task_handlers: dict[str, Callable] = {}


def register_task(task_type: str):
    """注册任务处理函数的装饰器"""

    def decorator(func):
        _task_handlers[task_type] = func
        return func

    return decorator


class TaskService:
    """任务服务"""

    def __init__(self, db: AsyncSession, session_factory=None):
        self.db = db
        self._session_factory = session_factory  # 可选：用于 session 失效时创建新 session
        # 取消状态缓存：避免频繁查 DB（流式处理中每个 batch 都查会很慢）
        # key: task_id, value: (cancelled: bool, last_check_time: float)
        self._cancel_cache: dict[str, tuple[bool, float]] = {}
        self._cancel_cache_ttl = 1.0  # 缓存 1 秒，取消操作本身就不要求毫秒级响应

    async def create_task(
        self,
        task_type: str,
        payload: dict,
        priority: int = 5,
    ) -> TaskDB:
        """创建任务"""
        task = TaskDB(
            id=generate_id(),
            type=task_type,
            status="pending",
            priority=priority,
            payload=payload,
            max_retries=settings.task_max_retries,
        )
        self.db.add(task)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(task)
        logger.info(f"Task created: {task.id} ({task_type})")
        return task

    async def get_task(self, task_id: str) -> TaskDB:
        """获取任务详情"""
        result = await self.db.execute(select(TaskDB).where(TaskDB.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            from app.core.exceptions import TaskNotFoundError

            raise TaskNotFoundError(task_id)
        return task

    async def list_tasks(
        self,
        page: int = 1,
        page_size: int = 20,
        task_type: str | None = None,
        status: str | None = None,
    ) -> tuple[list[TaskDB], int]:
        """任务列表"""
        query = select(TaskDB)

        if task_type:
            query = query.where(TaskDB.type == task_type)
        if status:
            query = query.where(TaskDB.status == status)

        count_query = select(func.count()).select_from(query.subquery())
        result = await self.db.execute(count_query)
        total = result.scalar_one()

        query = query.order_by(desc(TaskDB.created_at))
        query = query.offset((page - 1) * page_size).limit(page_size)
        result = await self.db.execute(query)
        items = result.scalars().all()

        return list(items), total

    async def update_progress(
        self,
        task_id: str,
        progress: float,
        detail: dict | None = None,
    ) -> None:
        """更新任务进度（带节流，避免过频繁的 DB 写入）"""
        task = await self.get_task(task_id)
        new_progress = max(0.0, min(1.0, progress))

        # 节流：进度变化小于 0.5% 且 detail 没变时，跳过 DB 写入
        prev_progress = task.progress or 0.0
        if abs(new_progress - prev_progress) < 0.005 and not detail:
            return

        task.progress = new_progress
        if detail is not None:
            # 合并 detail，保留已有字段
            if task.progress_detail and isinstance(task.progress_detail, dict):
                task.progress_detail.update(detail)
            else:
                task.progress_detail = detail
        await self.db.commit()

    async def run_task(self, task_id: str) -> None:
        """执行任务（在后台任务中调用）"""
        task = await self.get_task(task_id)
        task.status = "running"
        task.started_at = datetime.utcnow()
        await self.db.commit()

        handler = _task_handlers.get(task.type)
        if not handler:
            task.status = "failed"
            task.error_message = f"No handler registered for task type: {task.type}"
            task.completed_at = datetime.utcnow()
            await self.db.commit()
            return

        try:
            logger.info(f"Task starting: {task.id} ({task.type})")
            result = await handler(task.payload, task.id, self)
            # 完成前再次检查是否被取消
            if await self.is_task_cancelled(task.id):
                raise asyncio.CancelledError()
            task.status = "completed"
            task.progress = 1.0
            task.result = result
            task.completed_at = datetime.utcnow()
            await self.db.commit()
            logger.info(f"Task completed: {task.id}")
        except asyncio.CancelledError:
            logger.info(f"Task cancelled during execution: {task.id}")
            # 当前 session 可能已失效（cancel 发生在 DB await 中时），用 fallback 更新状态
            ok = await self._update_status_with_fallback(
                task.id,
                "cancelled",
                completed_at=datetime.utcnow(),
            )
            if not ok:
                logger.warning(
                    f"Could not persist cancelled status for task {task.id}. "
                    f"Will be recovered on next startup."
                )
            raise  # 继续抛出，让上层 worker 知道
        except Exception as e:
            logger.error(f"Task failed: {task.id}, error: {e}")
            logger.error(traceback.format_exc())

            task_id = task.id
            error_msg = str(e)[:500]

            # 确保 session 状态干净（出错时可能有未提交的脏数据）
            try:
                await self.db.rollback()
            except Exception:
                pass

            # 先检查是否是被取消的
            try:
                from sqlalchemy import text
                row = await self.db.execute(
                    text("SELECT status FROM tasks WHERE id = :tid"),
                    {"tid": task_id},
                )
                row = row.fetchone()
                if row and row[0] == "cancelled":
                    logger.info(f"Task {task_id} was cancelled (detected in error handler)")
                    return
            except Exception:
                pass

            # 直接用 SQL 更新任务状态，避免 session 对象状态问题
            from sqlalchemy import text

            # 先读取当前 retry_count
            try:
                row = await self.db.execute(
                    text("SELECT retry_count, max_retries FROM tasks WHERE id = :tid"),
                    {"tid": task_id},
                )
                row = row.fetchone()
                if not row:
                    logger.error(f"Task not found for status update: {task_id}")
                    return
                retry_count = row[0] + 1
                max_retries = row[1]
            except Exception as ex:
                logger.error(f"Failed to read task retry count: {ex}")
                return

            now = datetime.utcnow()
            if retry_count < max_retries:
                new_status = "pending"
                completed_at = None
            else:
                new_status = "failed"
                completed_at = now

            try:
                await self.db.execute(
                    text("""
                        UPDATE tasks
                        SET status = :status,
                            retry_count = :rc,
                            error_message = :err,
                            completed_at = :cat,
                            updated_at = :uat
                        WHERE id = :tid
                    """),
                    {
                        "status": new_status,
                        "rc": retry_count,
                        "err": error_msg,
                        "cat": completed_at,
                        "uat": now,
                        "tid": task_id,
                    },
                )
                await self.db.commit()
            except Exception as ex:
                logger.error(f"Failed to update task status: {ex}")
                return

            if new_status == "pending":
                # 稍后重试：把任务放回队列
                asyncio.create_task(self._delayed_retry(task_id))

    async def _delayed_retry(self, task_id: str) -> None:
        """延迟重试：把任务重新放回队列，由 worker 用新的 session 执行"""
        await asyncio.sleep(settings.task_retry_delay)
        try:
            await _task_queue.put(task_id)
            logger.info(f"Task requeued for retry: {task_id}")
        except Exception as e:
            logger.error(f"Failed to requeue task {task_id}: {e}")

    async def cancel_task(self, task_id: str) -> None:
        """取消任务"""
        task = await self.get_task(task_id)
        if task.status in ("running", "pending", "queued"):
            task.status = "cancelled"
            task.completed_at = datetime.utcnow()
            await self.db.commit()
            # 立即更新缓存，确保后续 check_cancelled 能立即感知到
            self._cancel_cache[task_id] = (True, time.perf_counter())
            logger.info(f"Task cancelled: {task_id}")
        else:
            from app.core.exceptions import BadRequestError
            raise BadRequestError(f"Cannot cancel task with status: {task.status}")

    async def is_task_cancelled(self, task_id: str) -> bool:
        """检查任务是否已取消（用于任务执行过程中的轮询检查）

        带 1 秒缓存，避免流式处理中每个 batch 都查 DB 导致性能下降。
        """
        now = time.perf_counter()

        # 检查缓存
        cached = self._cancel_cache.get(task_id)
        if cached is not None:
            cancelled, last_check = cached
            if now - last_check < self._cancel_cache_ttl:
                return cancelled

        # 缓存未命中或已过期，查 DB
        from sqlalchemy import text
        try:
            row = await self.db.execute(
                text("SELECT status FROM tasks WHERE id = :tid"),
                {"tid": task_id},
            )
            row = row.fetchone()
            if not row:
                cancelled = True
            else:
                cancelled = row[0] == "cancelled"
        except Exception:
            # DB 出错时保守处理：不认为已取消（避免误杀任务）
            cancelled = False

        # 更新缓存
        self._cancel_cache[task_id] = (cancelled, now)
        return cancelled

    async def check_cancelled(self, task_id: str) -> None:
        """检查任务是否已取消，如果已取消则抛出异常中止任务"""
        if await self.is_task_cancelled(task_id):
            logger.info(f"Task {task_id} was cancelled, aborting...")
            raise asyncio.CancelledError(f"Task {task_id} was cancelled")

    async def _update_status_with_fallback(self, task_id: str, status: str, **kwargs) -> bool:
        """
        更新任务状态，优先用当前 session，失败则创建新 session。
        用于 CancelledError/error 场景中当前 session 可能已失效的情况。
        """
        from sqlalchemy import text

        now = datetime.utcnow()
        fields = ["status = :status", "updated_at = :uat"]
        params = {"status": status, "uat": now, "tid": task_id}
        for k, v in kwargs.items():
            fields.append(f"{k} = :{k}")
            params[k] = v

        set_clause = ", ".join(fields)
        sql = f"UPDATE tasks SET {set_clause} WHERE id = :tid"

        # 先试当前 session
        try:
            await self.db.execute(text(sql), params)
            await self.db.commit()
            return True
        except Exception:
            pass

        # 当前 session 不行，尝试用新 session
        if self._session_factory:
            try:
                async with self._session_factory() as new_session:
                    await new_session.execute(text(sql), params)
                    await new_session.commit()
                return True
            except Exception as ex:
                logger.error(f"Failed to update task {task_id} status with new session: {ex}")
                return False

        return False


# ==========================================
# 全局任务调度器（后台线程执行）
# ==========================================

_task_queue: asyncio.Queue = asyncio.Queue()
_worker_tasks: list[asyncio.Task] = []
_running = False


async def start_task_workers(db_session_factory) -> None:
    """启动后台任务 Worker"""
    global _running, _worker_tasks
    if _running:
        return

    # 导入任务模块，触发 @register_task 装饰器注册 handler
    import app.tasks  # noqa: F401

    _running = True
    logger.info(f"Starting {settings.task_max_workers} task workers...")
    logger.info(f"Registered task handlers: {list(_task_handlers.keys())}")

    # 启动时恢复：把所有 running/pending 状态的任务重新入队
    # （异常退出时任务可能卡在 running 状态）
    async with db_session_factory() as session:
        from sqlalchemy import select

        from app.models.db.task import TaskDB

        result = await session.execute(
            select(TaskDB).where(TaskDB.status.in_(["running", "pending", "queued"]))
        )
        stuck_tasks = result.scalars().all()
        for t in stuck_tasks:
            if t.status == "running":
                t.status = "pending"
                t.retry_count = 0  # 崩溃恢复：重置重试计数，给任务公平的重试机会
                logger.info(f"Recovered stuck task: {t.id} ({t.type})")
        await session.commit()
        for t in stuck_tasks:
            await _task_queue.put(t.id)
        if stuck_tasks:
            logger.info(f"Recovered {len(stuck_tasks)} stuck tasks")

    async def worker(worker_id: int):
        logger.debug(f"Task worker {worker_id} started")
        while _running:
            try:
                task_id = await _task_queue.get()
                if task_id is None:
                    break

                # 每个任务创建独立的 DB session
                async with db_session_factory() as session:
                    service = TaskService(session, session_factory=db_session_factory)
                    try:
                        await service.run_task(task_id)
                    except asyncio.CancelledError:
                        # 任务被取消，状态已在 run_task 里用 fallback 尝试更新
                        logger.info(f"Worker {worker_id}: task {task_id} cancelled")
                    except Exception as e:
                        logger.error(f"Worker {worker_id} error: {e}")
                _task_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} unexpected error: {e}")
                await asyncio.sleep(1)

    _worker_tasks = [asyncio.create_task(worker(i)) for i in range(settings.task_max_workers)]


async def stop_task_workers() -> None:
    """停止任务 Worker"""
    global _running
    _running = False
    for _ in _worker_tasks:
        await _task_queue.put(None)
    for t in _worker_tasks:
        t.cancel()
    await asyncio.gather(*_worker_tasks, return_exceptions=True)
    logger.info("All task workers stopped")


async def enqueue_task(task_id: str) -> None:
    """将任务加入执行队列"""
    await _task_queue.put(task_id)
    logger.debug(f"Task enqueued: {task_id}")
