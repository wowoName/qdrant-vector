import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.smart_query.query_decomposer import QueryDecomposer


@pytest.mark.parametrize("response", ["invalid JSON", '{"sub_queries": []}'])
def test_invalid_response_reports_failure(response):
    decomposer = QueryDecomposer()
    decomposer._llm = SimpleNamespace(simple_chat=AsyncMock(return_value=response))
    result = asyncio.run(decomposer.decompose("查看王强的个人信息"))
    assert result.method == "failed"
    assert "内容无效" in result.error


def test_call_failure_stops_query_before_retrieval():
    from app.services.smart_query.orchestrator import SmartQueryOrchestrator

    decomposer = QueryDecomposer()
    decomposer._llm = SimpleNamespace(simple_chat=AsyncMock(side_effect=RuntimeError("secret key")))
    orchestrator = object.__new__(SmartQueryOrchestrator)
    orchestrator.collection_id = "test"
    orchestrator.query_decomposer = decomposer
    orchestrator._find_table_files = AsyncMock()
    result = asyncio.run(orchestrator.query("查看王强的个人信息"))
    assert "调用失败" in result.final_answer
    assert "secret key" not in result.final_answer
    assert result.debug_info["decomposition"]["method"] == "failed"
    orchestrator._find_table_files.assert_not_awaited()


def test_valid_single_intent_response_is_not_failure():
    decomposer = QueryDecomposer()
    decomposer._llm = SimpleNamespace(simple_chat=AsyncMock(return_value='{"is_multi_intent": false, "sub_queries": [{"query": "查看王强的个人信息"}]}'))
    result = asyncio.run(decomposer.decompose("查看王强的个人信息"))
    assert not result.error
    assert result.method == "single"
