from fastapi.testclient import TestClient

import main
from auth import CurrentUser, get_current_user


class FakeMemoryService:
    def __init__(self):
        self.searches = []
        self.adds = []

    def search(self, user_id, query, limit=5):
        self.searches.append((user_id, query, limit))
        return ["User prefers concise answers."]

    def add_turn(self, user_id, prompt, answer, conversation_id=None):
        self.adds.append((user_id, prompt, answer, conversation_id))
        return {"event_id": "evt-1", "status": "PENDING"}


def _fake_run_agent(calls):
    async def run(prompt, conversation=None, *, conversation_id=None, memory_context=""):
        calls["prompt"] = prompt
        calls["conversation_id"] = conversation_id
        calls["memory_context"] = memory_context
        return {
            "plan": {"type": "conversational"},
            "final_ans": "Hello.",
            "sources": [],
        }

    return run


def test_query_scopes_memory_to_authenticated_user(monkeypatch, auth_headers):
    memory = FakeMemoryService()
    calls = {}
    monkeypatch.setattr(main, "get_memory_service", lambda: memory)
    monkeypatch.setattr(main, "run_agent", _fake_run_agent(calls))
    client = TestClient(main.app)

    response = client.post(
        "/query",
        json={"user prompt": " hello "},
        headers={"X-Conversation-Id": "conv-1"},
    )

    assert response.status_code == 200
    assert memory.searches == [
        ("secrag:user:1", "hello", 5),
    ]
    assert calls["conversation_id"] == "1:conv-1"
    assert "User prefers concise answers." in calls["memory_context"]
    assert memory.adds == [
        ("secrag:user:1", "hello", "Hello.", "conv-1"),
    ]


def test_query_ignores_user_identity_from_request(monkeypatch):
    memory = FakeMemoryService()
    calls = {}
    monkeypatch.setattr(main, "get_memory_service", lambda: memory)
    monkeypatch.setattr(main, "run_agent", _fake_run_agent(calls))
    main.app.dependency_overrides[get_current_user] = lambda: CurrentUser(
        id=7,
        username="seven",
    )
    try:
        client = TestClient(main.app)
        response = client.post(
            "/query",
            json={"user prompt": "hello"},
            headers={"X-User-Id": "1", "X-Conversation-Id": "conv-1"},
        )
    finally:
        main.app.dependency_overrides.pop(get_current_user, None)

    assert response.status_code == 200
    assert memory.searches == [
        ("secrag:user:7", "hello", 5),
    ]
    assert calls["conversation_id"] == "7:conv-1"
    assert memory.adds[0][0] == "secrag:user:7"


def test_stream_scopes_memory_and_saves_final_answer(monkeypatch, auth_headers):
    memory = FakeMemoryService()
    calls = {}

    async def fake_stream(
        prompt,
        conversation=None,
        *,
        conversation_id=None,
        memory_context="",
    ):
        calls["conversation_id"] = conversation_id
        calls["memory_context"] = memory_context
        yield {
            "event": "done",
            "data": {"type": "retrieval", "answer": "Grounded answer.", "sources": []},
        }

    monkeypatch.setattr(main, "get_memory_service", lambda: memory)
    monkeypatch.setattr(main, "stream_agent", fake_stream)
    client = TestClient(main.app)

    response = client.post(
        "/query/stream",
        json={"user prompt": "revenue?"},
        headers={"X-Conversation-Id": "conv-9"},
    )

    assert response.status_code == 200
    assert calls["conversation_id"] == "1:conv-9"
    assert "User prefers concise answers." in calls["memory_context"]
    assert memory.searches == [("secrag:user:1", "revenue?", 5)]
    assert memory.adds == [
        ("secrag:user:1", "revenue?", "Grounded answer.", "conv-9"),
    ]


def test_query_does_not_store_blocked_answers(monkeypatch, auth_headers):
    memory = FakeMemoryService()

    async def blocked_run(
        prompt,
        conversation=None,
        *,
        conversation_id=None,
        memory_context="",
    ):
        return {
            "plan": {"type": "blocked"},
            "final_ans": "I can't help with that.",
            "sources": [],
        }

    monkeypatch.setattr(main, "get_memory_service", lambda: memory)
    monkeypatch.setattr(main, "run_agent", blocked_run)
    client = TestClient(main.app)

    response = client.post("/query", json={"user prompt": "bad request"})

    assert response.status_code == 200
    assert memory.adds == []
