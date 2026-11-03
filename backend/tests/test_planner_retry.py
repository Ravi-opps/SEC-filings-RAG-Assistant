import asyncio

import pytest

from retrieval_agent import (
    PlannerDecision,
    PlannerError,
    build_agent_graph,
    parse_planner_response,
)


def _state():
    return {
        "current_msg": "hello",
        "conversation": [],
        "memory_context": "",
        "plan": {},
        "status": "planning",
        "final_ans": "",
        "guardrail": {},
        "retrieval_query": "",
        "retrieved_context": "",
        "sources": [],
    }


def _conversationalist(_prompt, _history, _context):
    return "hello"


def test_planner_node_retries_after_non_json_output(monkeypatch):
    monkeypatch.delenv("PLANNER_MAX_ATTEMPTS", raising=False)
    calls = []

    def planner(prompt, history):
        calls.append(prompt)
        if len(calls) == 1:
            return "this is retrieval"
        return '{"type": "conversational"}'

    graph = build_agent_graph(planner, _conversationalist)
    result = asyncio.run(graph.ainvoke(_state()))

    assert len(calls) == 2
    assert result["plan"] == {"type": "conversational"}


def test_planner_node_retries_after_extra_json_keys(monkeypatch):
    monkeypatch.delenv("PLANNER_MAX_ATTEMPTS", raising=False)
    calls = []

    def planner(prompt, history):
        calls.append(prompt)
        if len(calls) == 1:
            return {"type": "retrieval", "reason": "because"}
        return {"type": "conversational"}

    graph = build_agent_graph(planner, _conversationalist)
    result = asyncio.run(graph.ainvoke(_state()))

    assert len(calls) == 2
    assert result["plan"] == {"type": "conversational"}


def test_planner_node_stops_after_max_attempts(monkeypatch):
    monkeypatch.setenv("PLANNER_MAX_ATTEMPTS", "2")
    calls = []

    def planner(prompt, history):
        calls.append(prompt)
        return {"route": "retrieval"}

    graph = build_agent_graph(planner, _conversationalist)
    with pytest.raises(PlannerError, match="after 2 attempts"):
        asyncio.run(graph.ainvoke(_state()))

    assert len(calls) == 2


def test_planner_node_does_not_retry_configuration_errors(monkeypatch):
    from retrieval_agent import PlannerConfigError

    monkeypatch.setenv("PLANNER_MAX_ATTEMPTS", "3")
    calls = []

    def planner(prompt, history):
        calls.append(prompt)
        raise PlannerConfigError("CLASSIFIER_URL is not configured")

    graph = build_agent_graph(planner, _conversationalist)
    with pytest.raises(PlannerConfigError):
        asyncio.run(graph.ainvoke(_state()))

    assert len(calls) == 1


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "here is the answer: {\"type\": \"retrieval\"}",
        '{"type": "retrieval", "extra": true}',
        '{"route": "retrieval"}',
        '{"type": "chat"}',
        "[]",
        "",
    ],
)
def test_parse_planner_response_rejects_non_schema_output(content):
    with pytest.raises(PlannerError):
        parse_planner_response(content)


def test_parse_planner_response_accepts_exact_schema():
    assert parse_planner_response('{"type": "retrieval"}') == PlannerDecision(
        type="retrieval"
    )
    assert parse_planner_response('{"type": "conversational"}') == PlannerDecision(
        type="conversational"
    )
