import main
from fastapi.testclient import TestClient
from retrieval_agent import PlannerConfigError


def test_query_stream_emits_sse_frames(monkeypatch, auth_headers):
    calls = {}

    async def fake_stream(
        prompt,
        conversation=None,
        *,
        conversation_id=None,
        memory_context="",
    ):
        calls["prompt"] = prompt
        calls["conversation_id"] = conversation_id
        calls["memory_context"] = memory_context
        yield {"event": "status", "data": {"stage": "retrieving"}}
        yield {"event": "sources", "data": [{"citation": 1}]}
        yield {"event": "delta", "data": {"text": "hi"}}
        yield {
            "event": "done",
            "data": {"type": "retrieval", "answer": "hi", "sources": []},
        }

    monkeypatch.setattr(main, "stream_agent", fake_stream)
    client = TestClient(main.app)

    response = client.post(
        "/query/stream",
        json={"user prompt": " hello "},
        headers={"X-Conversation-Id": "conv-1"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["cache-control"] == "no-cache, no-transform"
    assert calls == {
        "prompt": "hello",
        "conversation_id": "1:conv-1",
        "memory_context": "",
    }

    body = response.text
    assert 'event: status\ndata: {"stage": "accepted"}\n\n' in body
    assert 'event: status\ndata: {"stage": "retrieving"}\n\n' in body
    assert 'event: sources\ndata: [{"citation": 1}]\n\n' in body
    assert 'event: delta\ndata: {"text": "hi"}\n\n' in body
    assert body.rstrip().endswith(
        'event: done\ndata: {"type": "retrieval", "answer": "hi", "sources": []}'
    )


def test_query_stream_reports_config_errors_as_events(monkeypatch, auth_headers):
    async def fake_stream(
        prompt,
        conversation=None,
        *,
        conversation_id=None,
        memory_context="",
    ):
        raise PlannerConfigError("CLASSIFIER_URL is not configured")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "stream_agent", fake_stream)
    client = TestClient(main.app)

    response = client.post("/query/stream", json={"user prompt": "hello"})

    assert response.status_code == 200
    assert "event: error" in response.text
    assert '"status": 503' in response.text
    assert "CLASSIFIER_URL is not configured" in response.text


def test_query_stream_rejects_empty_prompt(auth_headers):
    client = TestClient(main.app)
    response = client.post("/query/stream", json={"user prompt": "   "})
    assert response.status_code == 422
