import pytest

from memory_service import (
    MEMORY_CONTEXT_HEADER,
    MemoryConfigError,
    MemoryService,
    format_memory_context,
    memory_enabled,
    memory_user_id,
)


class FakeClient:
    def __init__(self):
        self.search_calls = []
        self.add_calls = []
        self.search_payload = {
            "results": [
                {"memory": "User likes concise answers."},
                {"text": "User is researching AAPL."},
                "User prefers citations.",
            ]
        }

    def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        return self.search_payload

    def add(self, messages, **kwargs):
        self.add_calls.append((messages, kwargs))
        return {"event_id": "evt-1", "status": "PENDING"}


def test_memory_user_id_defaults_to_secrag_prefix(monkeypatch):
    monkeypatch.delenv("MEM0_USER_PREFIX", raising=False)
    assert memory_user_id(42) == "secrag:user:42"


def test_memory_user_id_respects_configured_prefix(monkeypatch):
    monkeypatch.setenv("MEM0_USER_PREFIX", "tenant:acct:")
    assert memory_user_id(42) == "tenant:acct:42"


def test_format_memory_context_is_empty_without_memories():
    assert format_memory_context([]) == ""
    assert format_memory_context(["", "   "]) == ""


def test_format_memory_context_marks_memories_as_untrusted():
    context = format_memory_context(["likes teal", "covers AAPL"])
    assert context.startswith(MEMORY_CONTEXT_HEADER)
    assert "- likes teal" in context
    assert "- covers AAPL" in context


def test_search_uses_user_filter_and_deduplicates_text():
    client = FakeClient()
    service = MemoryService(client=client)

    memories = service.search("secrag:user:7", "what do you remember?", limit=3)

    assert memories == [
        "User likes concise answers.",
        "User is researching AAPL.",
        "User prefers citations.",
    ]
    assert client.search_calls == [
        (
            "what do you remember?",
            {"filters": {"user_id": "secrag:user:7"}, "top_k": 3},
        )
    ]


def test_add_turn_uses_user_id_and_conversation_metadata():
    client = FakeClient()
    service = MemoryService(client=client)

    event = service.add_turn(
        "secrag:user:7",
        "My favorite color is teal.",
        "Noted.",
        "conv-1",
    )

    assert event == {"event_id": "evt-1", "status": "PENDING"}
    messages, kwargs = client.add_calls[0]
    assert messages == [
        {"role": "user", "content": "My favorite color is teal."},
        {"role": "assistant", "content": "Noted."},
    ]
    assert kwargs == {
        "user_id": "secrag:user:7",
        "metadata": {"source": "secrag", "conversation_id": "conv-1"},
    }


def test_add_turn_skips_empty_messages():
    client = FakeClient()
    service = MemoryService(client=client)

    assert service.add_turn("secrag:user:7", " ", "Noted.") == {}
    assert client.add_calls == []


def test_memory_service_requires_a_key(monkeypatch):
    monkeypatch.delenv("MEM0_KEY", raising=False)
    with pytest.raises(MemoryConfigError):
        MemoryService(api_key="")


def test_memory_enabled_requires_a_key(monkeypatch):
    monkeypatch.delenv("MEMORY_ENABLED", raising=False)
    monkeypatch.delenv("MEM0_KEY", raising=False)
    assert memory_enabled() is False

    monkeypatch.setenv("MEM0_KEY", "test-key")
    assert memory_enabled() is True

    monkeypatch.setenv("MEMORY_ENABLED", "false")
    assert memory_enabled() is False
