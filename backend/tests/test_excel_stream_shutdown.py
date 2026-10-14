import asyncio
import threading
from unittest.mock import Mock

import pytest

from app.processors.excel_processor import ExcelProcessor


def complete(coroutine):
    with pytest.raises(StopIteration) as result:
        coroutine.send(None)
    return result.value.value


def test_closing_stream_releases_producer_with_full_queue(monkeypatch):
    finished = threading.Event()

    def records():
        try:
            for value in range(10000):
                yield value
        finally:
            finished.set()

    async def execute(executor, function):
        return function()

    loop = Mock(run_in_executor=execute)
    monkeypatch.setattr(asyncio, "get_event_loop", lambda: loop)
    processor = object.__new__(ExcelProcessor)
    stream = processor._sync_gen_to_async(records, batch_size=1)
    assert complete(stream.__anext__()) == 0
    complete(stream.aclose())
    assert finished.wait(timeout=1.0), "Producer remained blocked on a full queue"
