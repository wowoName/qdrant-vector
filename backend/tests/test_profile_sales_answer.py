from unittest.mock import AsyncMock

import pytest

from app.services.smart_query.orchestrator import SmartQueryOrchestrator
from app.services.smart_query.synthesizer_agent import SynthesizerAgent
from app.services.smart_query.types import ColumnInfo, RetrievalHit, RetrievalSource, TableSchema


def schemas():
    return [
        TableSchema(table_name="employees", columns=[ColumnInfo(name=n) for n in
                    ["员工编号", "姓名", "性别", "部门", "职位", "入职日期"]]),
        TableSchema(table_name="sales", columns=[ColumnInfo(name=n) for n in
                    ["销售单号", "销售经理", "销售金额（元）", "产品"]]),
    ]


def test_profile_uses_schema_instead_of_generic_filename():
    orchestrator = object.__new__(SmartQueryOrchestrator)
    files = [{"file_name": "新建 XLSX 工作表.xlsx"}, {"file_name": "模拟销售明细_1000条.xlsx"}]
    assert orchestrator._rank_table_files(files, "给出王强的信息", schemas()) == files[:1]
    assert orchestrator._rank_table_files(files, "给出王强的销售金额是多少", schemas()) == files[1:]


def test_profile_without_roster_keeps_available_table():
    orchestrator = object.__new__(SmartQueryOrchestrator)
    files = [{"file_name": "销售A.xlsx"}, {"file_name": "销售B.xlsx"}]
    assert orchestrator._rank_table_files(files, "给出王强的信息", [schemas()[1]] * 2) == files


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("llm_failure", [False, True])
def test_profile_and_sales_survive_unavailable_llm(nested, llm_failure):
    profile_query = "给出王强的信息"
    sales_query = "给出王强的销售金额是多少"
    row = {"员工编号": "EMP01833", "姓名": "王强", "部门": "供应链部", "职位": "销售经理", "销售目标": 0, "在职": False}
    profile_payload = {"row_data": row} if nested else dict(row)
    profile_payload.update(sub_query=profile_query, source_file="新建 XLSX 工作表.xlsx", _total_count=1)
    hits = [
        RetrievalHit(source=RetrievalSource.SQL, payload=profile_payload),
        RetrievalHit(source=RetrievalSource.SQL, payload={
            "type": "aggregate", "operation": "sum", "target_column": "销售金额（元）",
            "value": 7294883.42, "unit": "元", "sub_query": sales_query,
            "source_file": "模拟销售明细_1000条.xlsx",
        }),
    ]
    agent = SynthesizerAgent()
    agent._get_llm = lambda: object() if llm_failure else None
    agent._llm_text = AsyncMock(side_effect=RuntimeError("HTTP 402: Insufficient Balance"))
    # All external I/O is mocked, so this coroutine completes without yielding.
    coroutine = agent.synthesize_multi_subquery("给出王强的信息以及销售金额", [profile_query, sales_query], hits)
    with pytest.raises(StopIteration) as completed:
        coroutine.send(None)
    answer = completed.value.value
    for expected in ["姓名=王强", "EMP01833", "部门=供应链部", "职位=销售经理", "销售目标=0", "在职=False", "7294883.42元"]:
        assert expected in answer
    assert "_total_count" not in answer
    assert "点击下载" not in answer
