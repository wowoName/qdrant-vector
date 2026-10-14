"""
任务服务 - 轻量异步任务管理
"""

import asyncio
import time
import traceback
from collections.abc import Callable

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models.db.task import TaskDB
from app.utils.datetime_utils import now
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

    def is_worker_shutdown(self) -> bool:
        return _shutting_down

    async def create_task(
        self,
        task_type: str,
        payload: dict,
        priority: int = 5,
        user_id: str | None = None,
    ) -> TaskDB:
        """创建任务（user_id 为任务归属用户，用于 SaaS 隔离）"""
        task = TaskDB(
            id=generate_id(),
            type=task_type,
            status="pending",
            priority=priority,
            payload=payload,
            user_id=user_id,
            max_retries=settings.task_max_retries,
        )
        self.db.add(task)
        await self.db.flush()
        await self.db.commit()
        await self.db.refresh(task)
        logger.info(f"Task created: {task.id} ({task_type}, user={user_id})")
        return task

    async def get_task(self, task_id: str, user_id: str | None = None) -> TaskDB:
        """获取任务详情

        user_id 不为 None 时校验任务归属（API 层使用，防止跨用户访问）。
        内部调用（run_task/update_progress 等）不传 user_id，不做归属校验。
        """
        result = await self.db.execute(select(TaskDB).where(TaskDB.id == task_id))
        task = result.scalar_one_or_none()
        if not task:
            from app.core.exceptions import TaskNotFoundError

            raise TaskNotFoundError(task_id)
        if user_id is not None and task.user_id != user_id:
            from app.core.exceptions import TaskNotFoundError

            raise TaskNotFoundError(task_id)
        return task

    async def list_tasks(
        self,
        page: int = 1,
        page_size: int = 20,
        task_type: str | None = None,
        status: str | None = None,
        user_id: str | None = None,
    ) -> tuple[list[TaskDB], int]:
        """任务列表（user_id 不为 None 时只返回该用户的任务）"""
        query = select(TaskDB)

        if user_id is not None:
            query = query.where(TaskDB.user_id == user_id)
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
            task.progress_detail = {**(task.progress_detail or {}), **detail}
        await self.db.commit()

    async def run_task(self, task_id: str) -> None:
        """执行任务（在后台任务中调用）"""
        task = await self.get_task(task_id)
        if task.status not in ("pending", "queued", "running"):
            return
        # A failed flush expires ORM attributes. Error handling must only use
        # these plain values until the broken transaction has been rolled back.
        task_type = task.type
        payload = dict(task.payload or {})
        task.status = "running"
        task.progress = 0.0
        task.progress_detail = {"stage": "starting"}
        task.error_message = None
        task.started_at = now()
        task.completed_at = None
        await self.db.commit()

        handler = _task_handlers.get(task_type)
        try:
            if not handler:
                raise RuntimeError(f"No handler registered for task type: {task_type}")
            logger.info(f"Task starting: {task_id} ({task_type})")
            result = await handler(payload, task_id, self)
            # 完成前再次检查是否被取消
            if await self.is_task_cancelled(task_id):
                raise asyncio.CancelledError()
            task.status = "completed"
            task.progress = 1.0
            task.result = result
            task.completed_at = now()
            await self.db.commit()
            logger.info(f"Task completed: {task_id}")
        except asyncio.CancelledError:
            logger.info(f"Task cancelled during execution: {task_id}")
            # 先结束当前事务，避免在失败/被取消的事务上继续写入状态。
            try:
                await self.db.rollback()
            except Exception:
                logger.warning(f"Failed to roll back interrupted task {task_id}", exc_info=True)
            # Hot reload/shutdown is an interruption, not a user's cancellation.
            # Leave it recoverable by start_task_workers on the next startup.
            status = "pending" if self.is_worker_shutdown() else "cancelled"
            # 当前 session 可能已失效（cancel 发生在 DB await 中时），用 fallback 更新状态
            ok = await self._update_status_with_fallback(
                task_id,
                status,
                completed_at=None if status == "pending" else now(),
            )
            if not ok:
                logger.warning(
                    f"Could not persist cancelled status for task {task_id}. "
                    f"Will be recovered on next startup."
                )
            raise  # 继续抛出，让上层 worker 知道
        except Exception as e:
            logger.error(f"Task failed: {task_id}, error: {e}")
            logger.error(traceback.format_exc())

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

            now_ts = now()
            if retry_count < max_retries:
                new_status = "pending"
                completed_at = None
            else:
                new_status = "failed"
                completed_at = now_ts

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
                        "uat": now_ts,
                        "tid": task_id,
                    },
                )
                if task_type == "document_process":
                    await self._set_document_failure(payload, error_msg, new_status == "failed")
                await self.db.commit()
            except Exception as ex:
                logger.error(f"Failed to update task status: {ex}")
                return

            if new_status == "pending":
                # 稍后重试：把任务放回队列
                retry_task = asyncio.create_task(self._delayed_retry(task_id), name=f"task-retry-{task_id}")
                _retry_tasks.add(retry_task)
                retry_task.add_done_callback(_retry_tasks.discard)

    async def _set_document_failure(self, payload: dict, error: str, terminal: bool) -> None:
        """Keep document/version status aligned with the processing task."""
        from sqlalchemy import text

        params = {
            "vid": payload.get("version_id"), "did": payload.get("document_id"),
            "status": "failed" if terminal else "processing", "error": error,
        }
        await self.db.execute(text("""
            UPDATE document_versions SET status = :status, error_message = :error
            WHERE id = :vid AND document_id = :did
        """), params)
        # An older task must not overwrite the state of a newer upload.
        await self.db.execute(text("""
            UPDATE documents SET status = :status, error_message = :error
            WHERE id = :did AND :vid = (
                SELECT id FROM document_versions WHERE document_id = :did
                ORDER BY version_num DESC LIMIT 1
            )
        """), params)

    async def _delayed_retry(self, task_id: str) -> None:
        """延迟重试：把任务重新放回队列，由 worker 用新的 session 执行"""
        await asyncio.sleep(settings.task_retry_delay)
        if _shutting_down:
            return
        try:
            await _task_queue.put(task_id)
            logger.info(f"Task requeued for retry: {task_id}")
        except Exception as e:
            logger.error(f"Failed to requeue task {task_id}: {e}")

    async def cancel_task(self, task_id: str, user_id: str | None = None) -> None:
        """取消任务（user_id 不为 None 时校验任务归属）"""
        task = await self.get_task(task_id, user_id=user_id)
        if task.status in ("running", "pending", "queued"):
            task.status = "cancelled"
            task.completed_at = now()
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
        now_ts = time.perf_counter()

        # 检查缓存
        cached = self._cancel_cache.get(task_id)
        if cached is not None:
            cancelled, last_check = cached
            if now_ts - last_check < self._cancel_cache_ttl:
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
        self._cancel_cache[task_id] = (cancelled, now_ts)
        return cancelled

    async def check_cancelled(self, task_id: str) -> None:
        """检查任务是否已取消，如果已取消则抛出异常中止任务"""
        if self.is_worker_shutdown():
            # 在批次边界退出，不向正在执行的数据库操作注入取消。
            raise asyncio.CancelledError("Worker shutdown")
        if await self.is_task_cancelled(task_id):
            logger.info(f"Task {task_id} was cancelled, aborting...")
            raise asyncio.CancelledError(f"Task {task_id} was cancelled")

    async def _update_status_with_fallback(self, task_id: str, status: str, **kwargs) -> bool:
        """
        更新任务状态，优先用当前 session，失败则创建新 session。
        用于 CancelledError/error 场景中当前 session 可能已失效的情况。
        """
        from sqlalchemy import text

        now_ts = now()
        fields = ["status = :status", "updated_at = :uat"]
        params = {"status": status, "uat": now_ts, "tid": task_id}
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
_retry_tasks: set[asyncio.Task] = set()
_running = False
_shutting_down = False


async def start_task_workers(db_session_factory) -> None:
    """启动后台任务 Worker"""
    global _running, _worker_tasks, _shutting_down
    if _running:
        return

    # 导入任务模块，触发 @register_task 装饰器注册 handler
    import app.tasks  # noqa: F401

    _running = True
    _shutting_down = False
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
                if task_id is None or not _running:
                    _task_queue.task_done()
                    break

                # 每个任务创建独立的 DB session
                try:
                    async with db_session_factory() as session:
                        service = TaskService(session, session_factory=db_session_factory)
                        try:
                            await service.run_task(task_id)
                        except asyncio.CancelledError:
                            logger.info(f"Worker {worker_id}: task {task_id} cancelled")
                            if _shutting_down:
                                raise
                        except Exception as e:
                            logger.error(f"Worker {worker_id} error: {e}")
                finally:
                    _task_queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker {worker_id} unexpected error: {e}")
                await asyncio.sleep(1)

    _worker_tasks = [asyncio.create_task(worker(i), name=f"task-worker-{i}") for i in range(settings.task_max_workers)]


async def stop_task_workers(timeout: float = 10.0) -> None:
    """停止任务 Worker"""
    global _running, _shutting_down
    _shutting_down = True
    _running = False
    tasks = set(_worker_tasks) | _retry_tasks
    for t in _retry_tasks:
        t.cancel()
    # 唤醒空闲 Worker；运行中的 Worker 在下一次 check_cancelled 时退出。
    for _ in _worker_tasks:
        _task_queue.put_nowait(None)
    if tasks:
        done, pending = await asyncio.wait(tasks, timeout=timeout)
        for t in done:
            if not t.cancelled():
                t.exception()  # 读取异常，避免未处理任务告警
        if pending:
            for t in pending:
                stack = t.get_stack()
                location = ", ".join(f"{frame.f_code.co_filename}:{frame.f_lineno}" for frame in stack)
                logger.warning(f"Graceful shutdown timed out waiting for {t.get_name()}: {location}; cancelling task")
                t.cancel()
            # 只强制取消一次，留出事务回滚和连接关闭时间，避免再次打断清理。
            _, pending = await asyncio.wait(pending, timeout=timeout)
            if pending:
                logger.error(f"Workers still pending after cancellation: {[t.get_name() for t in pending]}")
                raise TimeoutError("Task workers did not finish shutdown cleanup")
    _worker_tasks.clear()
    _retry_tasks.clear()
    # 未执行的任务已持久化，下次启动会重新入队，清除旧队列避免重复执行。
    while not _task_queue.empty():
        _task_queue.get_nowait()
        _task_queue.task_done()
    logger.info("All task workers stopped")


async def enqueue_task(task_id: str) -> None:
    """将任务加入执行队列"""
    await _task_queue.put(task_id)
    logger.debug(f"Task enqueued: {task_id}")
