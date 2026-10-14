from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.query_state import QueryStateResolver, StateDelta, apply_delta, extract_profile_subject
from app.services.smart_query.orchestrator import SmartQueryOrchestrator
from app.services.smart_query.result_fusion import ResultFusion
from app.services.smart_query.router_agent import RouterAgent
from app.services.smart_query.synthesizer_agent import SynthesizerAgent
from app.services.smart_query.types import (
    ColumnInfo, FilterInfo, MultiRetrievalResult, QueryIntent, QueryOperation, QueryPlan,
    RetrievalHit, RetrievalSource, TableSchema,
)
from app.services.smart_query.vector_retriever import VectorRetriever


def complete(coroutine):
    with pytest.raises(StopIteration) as result:
        coroutine.send(None)
    return result.value.value


@pytest.mark.parametrize("query", ["王强信息 给出", "给出王强的信息", "查看王和平的个人信息"])
def test_explicit_profile_starts_new_business_scope(query):
    previous = {"filters": [{"column": "销售经理", "value": "王强"}], "group_by": "客户"}
    resolver = QueryStateResolver(llm=SimpleNamespace(simple_chat=AsyncMock(side_effect=RuntimeError("402"))))
    delta = complete(resolver.resolve(query, previous))
    assert delta.operation == "new_query"
    assert apply_delta(previous, delta) == {"filters": []}


@pytest.mark.parametrize("query", ["他的个人信息", "他们的信息", "给出王强的信息以及销售金额", "客户信息", "详细信息", "基本信息"])
def test_followups_and_multiple_intents_not_misclassified(query):
    assert extract_profile_subject(query) is None


def test_new_query_delta_clears_old_filters():
    assert apply_delta({"filters": [{"column": "职位", "value": "销售经理"}]}, StateDelta(operation="new_query")) == {"filters": []}


def test_new_query_clears_persisted_scope():
    from app.services.chat_service import ChatService

    service = object.__new__(ChatService)
    service._scope_enabled = lambda: True
    service.conv_repo = SimpleNamespace(set_conversation_state=AsyncMock())
    result = complete(service._apply_state_delta("conversation", {"filters": [{"column": "销售经理", "value": "王强"}]}, StateDelta(operation="new_query")))
    assert result is None
    service.conv_repo.set_conversation_state.assert_awaited_once_with("conversation", {"filters": []})


def test_profile_plan_does_not_inherit_nonexistent_sales_column():
    schema = TableSchema(table_name="staff", columns=[ColumnInfo(name="姓名"), ColumnInfo(name="部门")])
    plan = QueryPlan(intent=QueryIntent.HYBRID, operation=QueryOperation.UNKNOWN, filters=[FilterInfo(column="姓名", value="王强")])
    result = RouterAgent()._postprocess_plan(
        "王强信息 给出", schema, plan,
        inherited_state={"filters": [{"column": "销售经理", "value": "王强"}]},
    )
    assert result.operation == QueryOperation.DETAIL
    assert result.needs_sql and result.confidence >= 0.8
    assert [f.column for f in result.filters] == ["姓名"]


@pytest.mark.parametrize("strategy", ["rrf", "weighted_score"])
def test_two_retrieval_routes_merge_but_other_documents_remain(strategy):
    content = "姓名：王强；职位：销售经理"
    vector = RetrievalHit(source=RetrievalSource.VECTOR, document_id="a", record_id="chunk", content=content, score=0.9)
    keyword = RetrievalHit(source=RetrievalSource.KEYWORD, document_id="a", record_id="record", content=content, score=1)
    other = RetrievalHit(source=RetrievalSource.VECTOR, document_id="b", record_id="chunk-b", content=content, score=0.9)
    result = ResultFusion(strategy).fuse([], [vector, other], [keyword])
    assert len(result.merged_hits) == 2
    assert {h.document_id for h in result.merged_hits} == {"a", "b"}


def test_vector_search_uses_requested_collection_and_visibility_not_primary_document():
    service = SimpleNamespace(search=AsyncMock(return_value=SimpleNamespace(results=[])))
    retriever = VectorRetriever(vector_service=service, visible_ids=["tenant"])
    complete(retriever.retrieve("王强信息", QueryPlan(intent=QueryIntent.HYBRID, operation=QueryOperation.UNKNOWN), TableSchema(table_name="first", document_id="first-document"), collection_id="all"))
    args, kwargs = service.search.call_args
    assert args[0] == "all"
    assert args[1].document_id is None
    assert kwargs["visible_ids"] == ["tenant"]


def test_profile_answer_preserves_each_source_and_complete_short_text():
    hits = [
        RetrievalHit(source=RetrievalSource.SQL, document_id="a", content="姓名：王强；职位：销售经理", metadata={"table_name": "员工甲.xlsx", "collection_id": "one"}),
        RetrievalHit(source=RetrievalSource.SQL, document_id="b", content="姓名：王强；职位：物流专员", metadata={"table_name": "员工乙.xlsx", "collection_id": "two"}),
    ]
    agent = SynthesizerAgent()
    answer, refs = complete(agent.synthesize_multi("王强信息 给出", QueryPlan(intent=QueryIntent.STRUCTURED, operation=QueryOperation.DETAIL), MultiRetrievalResult(sql_hits=hits)))
    assert "员工甲.xlsx" in answer and "员工乙.xlsx" in answer
    assert "物流专员" in answer and "销售经理" in answer
    assert "未知文档" not in answer and "..." not in answer
    assert {r["collection_id"] for r in refs} == {"one", "two"}


def test_terse_profile_selects_all_rosters_even_with_generic_filenames():
    orchestrator = object.__new__(SmartQueryOrchestrator)
    files = [{"file_name": name} for name in ["员工信息.xlsx", "新建.xlsx", "销售.xlsx"]]
    roster = TableSchema(table_name="staff", columns=[ColumnInfo(name=n) for n in ["姓名", "部门", "职位"]])
    sales = TableSchema(table_name="sales", columns=[ColumnInfo(name=n) for n in ["销售经理", "销售金额"]])
    assert orchestrator._rank_table_files(files, "王强信息 给出") == files
    assert orchestrator._rank_table_files(files, "王强信息 给出", [roster, roster, sales]) == files[:2]
