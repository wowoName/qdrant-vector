"""Patient boundaries and missing clinical values must survive indexing."""
from openpyxl import Workbook

from app.chunkers.base import RecordItem
from app.processors.excel_processor import ExcelProcessor


def test_blank_clinical_cells_do_not_inherit_previous_patient(tmp_path):
    path = tmp_path / "medical.xlsx"
    wb = Workbook()
    wb.active.append(["病历号", "诊断", "用药"])
    wb.active.append(["001", "肺炎", "抗生素"])
    wb.active.append(["002", None, None])
    wb.save(path)
    wb.close()
    processor = ExcelProcessor()
    processor._config_cache = {"mode": "record"}
    rows = list(processor._record_stream_xlsx_sync(str(path), "doc"))
    assert rows[1].data == {"病历号": "002", "诊断": "", "用药": ""}
    assert "肺炎" not in rows[1].text


def test_long_record_chunks_preserve_tail_and_source():
    processor = ExcelProcessor()
    processor._config_cache = {"record_chunk_size": 200, "record_chunk_overlap": 20}
    data = {"病历号": "002", "现病史": "反复咳嗽。" * 100, "治疗计划": "末尾治疗信息"}
    record = RecordItem("doc:0:1", "病历", 1, data=data,
                        text="；".join(f"{key}：{value}" for key, value in data.items()),
                        metadata={"sheet_index": 0})
    chunks = processor._record_to_chunks(record, 5)
    assert len(chunks) > 1
    assert all(len(chunk.text) <= 200 for chunk in chunks)
    assert all("病历号：002" in chunk.text for chunk in chunks)
    assert "末尾治疗信息" in chunks[-1].text
    assert [chunk.index for chunk in chunks] == list(range(5, 5 + len(chunks)))
    assert all(chunk.metadata["row_number"] == 1 for chunk in chunks)


def test_short_record_remains_one_chunk():
    processor = ExcelProcessor()
    record = RecordItem("doc:0:0", "病历", 0, text="诊断：肺炎")
    chunks = processor._record_to_chunks(record, 0)
    assert len(chunks) == 1
    assert chunks[0].text == record.text


def test_windows_loop_factory_uses_proactor():
    import sys
    import asyncio
    from app.core.event_loop import create_event_loop
    loop = create_event_loop()
    try:
        if sys.platform == "win32":
            assert isinstance(loop, asyncio.ProactorEventLoop)
    finally:
        loop.close()


def test_windows_entrypoint_does_not_transfer_reload_socket(monkeypatch):
    import sys
    from unittest.mock import Mock
    import uvicorn
    from app.main import main
    if sys.platform != "win32":
        return
    runner = Mock()
    monkeypatch.setattr(uvicorn, "run", runner)
    main()
    kwargs = runner.call_args.kwargs
    assert kwargs["reload"] is False
    assert kwargs["workers"] == 1
    assert kwargs["loop"] == "app.core.event_loop:create_event_loop"
