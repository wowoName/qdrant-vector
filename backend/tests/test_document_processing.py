"""Exercise real SQL transactions and Excel parsing without external services."""
from unittest.mock import AsyncMock, Mock
import asyncio

import pytest
from openpyxl import Workbook
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.models.db.document import DocumentDB, DocumentVersionDB
from app.models.db.document_record import DocumentRecordDB
from app.models.db.task import TaskDB
from app.processors.excel_processor import ExcelProcessor
from app.services.document_record_service import DocumentRecordService
from app.services.document_tasks import _import_records_for_document
from app.services.task_service import TaskService, _task_handlers


def complete(coroutine):
    # The DB adapter and mocked external calls finish synchronously; no sockets
    # or Windows event-loop self-pipe are needed for these transaction tests.
    with pytest.raises(StopIteration) as result:
        coroutine.send(None)
    return result.value.value


class AsyncSessionAdapter:
    def __init__(self, session):
        self.session = session

    def add_all(self, rows):
        self.session.add_all(rows)

    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})

    async def scalar(self, statement):
        return self.session.scalar(statement)

    async def flush(self):
        self.session.flush()

    async def commit(self):
        self.session.commit()

    async def rollback(self):
        self.session.rollback()


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    for model in [DocumentDB, DocumentVersionDB, DocumentRecordDB, TaskDB]:
        model.__table__.create(engine)
    with Session(engine, expire_on_commit=False) as session:
        session.add(DocumentDB(id="doc", collection_id="collection", name="test.xlsx", file_type="xlsx", status="processing"))
        session.add(DocumentVersionDB(id="version", document_id="doc", version_num=1, storage_path="test.xlsx"))
        session.commit()
        yield AsyncSessionAdapter(session)
    engine.dispose()


def row(version="old", index=0):
    return DocumentRecordDB(document_id="doc", version_id=version, collection_id="collection",
                            sheet_name="Sheet", row_number=index, data_json='{"姓名":"旧数据"}')


def test_excel_reupload_and_retry_replace_snapshot(db, tmp_path, monkeypatch):
    path = tmp_path / "employees.xlsx"
    workbook = Workbook()
    workbook.active.append(["姓名", "部门"])
    workbook.active.append(["王强", "供应链部"])
    workbook.active.append(["李明", "销售部"])
    workbook.save(path)
    workbook.close()

    async def inline_bridge(self, generator, *args, **kwargs):
        for item in generator(*args, **kwargs):
            yield item

    monkeypatch.setattr(ExcelProcessor, "_sync_gen_to_async", inline_bridge)
    db.session.add(row())
    db.session.commit()
    service = DocumentRecordService(db, "collection")
    for version in ["version", "version", "next-version"]:
        assert complete(service.import_excel(str(path), "doc", version, batch_size=1)) == 2
        db.session.commit()
        records = db.session.scalars(select(DocumentRecordDB)).all()
        assert len(records) == 2
        assert {record.version_id for record in records} == {version}
        assert {record.data["姓名"] for record in records} == {"王强", "李明"}


def test_failed_replacement_restores_previous_records(db, monkeypatch):
    from app.chunkers.base import RecordItem

    db.session.add(row())
    db.session.commit()

    async def broken_stream(*args):
        yield RecordItem("new", "Sheet", 0, data={"姓名": "新数据"})
        raise RuntimeError("Excel read failed")

    monkeypatch.setattr(ExcelProcessor, "record_stream", broken_stream)
    with pytest.raises(RuntimeError, match="Excel read failed"):
        complete(DocumentRecordService(db, "collection").import_excel("test.xlsx", "doc", "version", batch_size=1))
    db.session.rollback()
    records = db.session.scalars(select(DocumentRecordDB)).all()
    assert len(records) == 1
    assert records[0].version_id == "old"
    assert records[0].data["姓名"] == "旧数据"


def test_import_errors_propagate(monkeypatch):
    monkeypatch.setattr(DocumentRecordService, "import_excel", AsyncMock(side_effect=RuntimeError("insert failed")))
    with pytest.raises(RuntimeError, match="insert failed"):
        complete(_import_records_for_document(None, "test.xlsx", "doc", "version", "collection", "xlsx"))


@pytest.mark.parametrize("max_retries, task_status, doc_status", [(1, "failed", "failed"), (3, "pending", "processing")])
def test_flush_failure_persists_failed_task_and_document(db, monkeypatch, max_retries, task_status, doc_status):
    task = TaskDB(id="task", type="document_process", status="pending", max_retries=max_retries,
                  payload={"document_id": "doc", "version_id": "version"})
    db.session.add_all([task, row()])
    db.session.commit()

    async def failing_handler(payload, task_id, service):
        # A real failed flush expires TaskDB too. Reading task.id here would
        # raise PendingRollbackError and leave its persisted state 'running'.
        db.session.add(row("version"))
        await db.flush()

    monkeypatch.setitem(_task_handlers, "document_process", failing_handler)
    scheduled = []

    def schedule(coroutine, **kwargs):
        scheduled.append(coroutine)
        coroutine.close()
        return Mock()

    monkeypatch.setattr("app.services.task_service._retry_tasks", set())
    monkeypatch.setattr("app.services.task_service.asyncio.create_task", schedule)
    complete(TaskService(db).run_task("task"))
    db.session.expire_all()
    assert db.session.get(TaskDB, "task").status == task_status
    assert db.session.get(TaskDB, "task").retry_count == 1
    assert db.session.get(DocumentDB, "doc").status == doc_status
    assert db.session.get(DocumentVersionDB, "version").status == doc_status
    assert len(scheduled) == (1 if max_retries > 1 else 0)


def test_progress_detail_is_persisted_when_percentage_unchanged(db):
    db.session.add(TaskDB(id="task", type="document_process", progress=0.3,
                          progress_detail={"stage": "embedding", "total": 10}))
    db.session.commit()
    complete(TaskService(db).update_progress("task", 0.3, {"processed": 2}))
    db.session.expire_all()
    assert db.session.get(TaskDB, "task").progress_detail == {
        "stage": "embedding", "total": 10, "processed": 2,
    }


def test_older_failure_does_not_override_newer_upload(db):
    db.session.add(DocumentVersionDB(id="newer", document_id="doc", version_num=2, storage_path="new.xlsx"))
    db.session.commit()
    complete(TaskService(db)._set_document_failure({"document_id": "doc", "version_id": "version"}, "failed", True))
    db.session.commit()
    db.session.expire_all()
    assert db.session.get(DocumentDB, "doc").status == "processing"
    assert db.session.get(DocumentVersionDB, "version").status == "failed"


@pytest.mark.parametrize("shutdown, expected", [(True, "pending"), (False, "cancelled")])
def test_shutdown_interruption_remains_recoverable(db, monkeypatch, shutdown, expected):
    db.session.add(TaskDB(id="task", type="document_process", status="pending", payload={}))
    db.session.commit()

    async def interrupted(*args):
        raise asyncio.CancelledError()

    monkeypatch.setitem(_task_handlers, "document_process", interrupted)
    monkeypatch.setattr("app.services.task_service._shutting_down", shutdown)
    with pytest.raises(asyncio.CancelledError):
        complete(TaskService(db).run_task("task"))
    db.session.expire_all()
    task = db.session.get(TaskDB, "task")
    assert task.status == expected
    assert (task.completed_at is None) == shutdown
