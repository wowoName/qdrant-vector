import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import task_service


def complete(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Unexpected asynchronous I/O")


def test_shutdown_allows_grace_period_then_cancels_once(monkeypatch):
    worker = Mock()
    worker.get_stack.return_value = []
    worker.get_name.return_value = "task-worker-0"
    wait = AsyncMock(side_effect=[(set(), {worker}), ({worker}, set())])
    monkeypatch.setattr(task_service, "_worker_tasks", [worker])
    monkeypatch.setattr(task_service, "_retry_tasks", set())
    monkeypatch.setattr(task_service, "_task_queue", asyncio.Queue())
    monkeypatch.setattr(task_service, "_running", True)
    monkeypatch.setattr(task_service, "_shutting_down", False)
    monkeypatch.setattr(task_service.asyncio, "wait", wait)
    complete(task_service.stop_task_workers(timeout=0.1))
    assert worker.cancel.call_count == 1
    assert wait.await_args_list[0].kwargs["timeout"] == 0.1
    assert wait.await_args_list[1].kwargs["timeout"] == 0.1
    assert not task_service._worker_tasks
    assert task_service._shutting_down
    assert not task_service._running


def test_shutdown_cancels_delayed_retries_and_does_not_queue_sentinels(monkeypatch):
    retry = Mock()
    retry.cancelled.return_value = True
    wait = AsyncMock(return_value=({retry}, set()))
    queue = asyncio.Queue()
    monkeypatch.setattr(task_service, "_worker_tasks", [])
    monkeypatch.setattr(task_service, "_retry_tasks", {retry})
    monkeypatch.setattr(task_service, "_task_queue", queue)
    monkeypatch.setattr(task_service, "_running", True)
    monkeypatch.setattr(task_service, "_shutting_down", False)
    monkeypatch.setattr(task_service.asyncio, "wait", wait)
    complete(task_service.stop_task_workers())
    retry.cancel.assert_called_once()
    assert queue.empty()
    assert not task_service._retry_tasks


def test_cooperative_shutdown_does_not_cancel_worker(monkeypatch):
    worker = Mock()
    worker.cancelled.return_value = False
    queue = asyncio.Queue()
    queue.put_nowait("persisted-task")
    monkeypatch.setattr(task_service, "_worker_tasks", [worker])
    monkeypatch.setattr(task_service, "_retry_tasks", set())
    monkeypatch.setattr(task_service, "_task_queue", queue)
    monkeypatch.setattr(task_service, "_running", True)
    monkeypatch.setattr(task_service, "_shutting_down", False)
    monkeypatch.setattr(task_service.asyncio, "wait", AsyncMock(return_value=({worker}, set())))
    complete(task_service.stop_task_workers())
    worker.cancel.assert_not_called()
    assert queue.empty()
    assert queue._unfinished_tasks == 0


def test_shutdown_checkpoint_does_not_query_database(monkeypatch):
    monkeypatch.setattr(task_service, "_shutting_down", True)
    service = task_service.TaskService(Mock())
    service.is_task_cancelled = AsyncMock()
    with pytest.raises(asyncio.CancelledError, match="Worker shutdown"):
        complete(service.check_cancelled("task"))
    service.is_task_cancelled.assert_not_awaited()


def test_cleanup_timeout_is_reported_without_repeated_cancellation(monkeypatch):
    worker = Mock()
    worker.get_stack.return_value = []
    monkeypatch.setattr(task_service, "_worker_tasks", [worker])
    monkeypatch.setattr(task_service, "_retry_tasks", set())
    monkeypatch.setattr(task_service, "_task_queue", asyncio.Queue())
    monkeypatch.setattr(task_service, "_running", True)
    monkeypatch.setattr(task_service, "_shutting_down", False)
    monkeypatch.setattr(task_service.asyncio, "wait", AsyncMock(return_value=(set(), {worker})))
    with pytest.raises(TimeoutError):
        complete(task_service.stop_task_workers(timeout=0.1))
    worker.cancel.assert_called_once()
