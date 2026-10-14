import asyncio
from types import coroutine
from unittest.mock import AsyncMock

import pytest

from app.providers.embedding.huggingface_embedding import HuggingFaceEmbedding


@coroutine
def pause():
    yield "waiting"


class CooperativeLock:
    def __init__(self):
        self.locked = False

    async def __aenter__(self):
        while self.locked:
            await pause()
        self.locked = True

    async def __aexit__(self, *args):
        self.locked = False


def finish(task):
    with pytest.raises(StopIteration) as result:
        task.send(None)
    return result.value.value


def test_concurrent_callers_load_only_one_model(monkeypatch):

    async def load():
        await pause()

    loader = AsyncMock(side_effect=load)
    monkeypatch.setattr(HuggingFaceEmbedding, "_instance", None)
    monkeypatch.setattr(HuggingFaceEmbedding, "_model_name", None)
    monkeypatch.setattr(HuggingFaceEmbedding, "_instance_lock", CooperativeLock())
    monkeypatch.setattr(HuggingFaceEmbedding, "_load_model", loader)
    first = HuggingFaceEmbedding.get_instance()
    second = HuggingFaceEmbedding.get_instance()
    assert first.send(None) == "waiting"
    assert second.send(None) == "waiting"
    assert loader.await_count == 1
    assert finish(first) is finish(second)
    assert loader.await_count == 1


def test_failed_load_can_be_retried(monkeypatch):
    loader = AsyncMock(side_effect=[RuntimeError("load failed"), None])
    monkeypatch.setattr(HuggingFaceEmbedding, "_instance", None)
    monkeypatch.setattr(HuggingFaceEmbedding, "_model_name", None)
    monkeypatch.setattr(HuggingFaceEmbedding, "_instance_lock", CooperativeLock())
    monkeypatch.setattr(HuggingFaceEmbedding, "_load_model", loader)
    with pytest.raises(RuntimeError, match="load failed"):
        HuggingFaceEmbedding.get_instance().send(None)
    assert HuggingFaceEmbedding._instance is None
    assert finish(HuggingFaceEmbedding.get_instance()) is not None
    assert loader.await_count == 2
