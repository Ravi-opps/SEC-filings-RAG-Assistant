import asyncio
import json

import httpx
import pytest

from retrieval_agent import LLMResponder, ResponderError


def _responder(handler):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return (
        LLMResponder(
            url="https://llm.test/v1/responses",
            api_key="test-key",
            client=client,
        ),
        client,
    )


def _collect(responder):
    async def run():
        return [piece async for piece in responder.respond_stream("hi", [])]

    return asyncio.run(run())


def test_respond_stream_parses_responses_sse_deltas():
    seen = {}

    def handler(request):
        seen["body"] = json.loads(request.content)
        events = [
            {"type": "response.output_text.delta", "delta": "Hello"},
            {"type": "response.output_text.delta", "delta": " world"},
            {"type": "response.output_text.done", "text": "Hello world"},
            {"type": "response.completed", "response": {"output_text": "Hello world"}},
        ]
        body = "".join(
            f"data: {json.dumps(event)}\n\n" for event in events
        ) + "data: [DONE]\n\n"
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    responder, client = _responder(handler)
    try:
        pieces = _collect(responder)
    finally:
        asyncio.run(client.aclose())

    assert pieces == ["Hello", " world"]
    assert seen["body"]["stream"] is True
    assert seen["body"]["model"] == "gpt-6-sol"


def test_respond_stream_parses_chat_completion_chunks():
    def handler(request):
        events = [
            {"choices": [{"delta": {"content": "A"}}]},
            {"choices": [{"delta": {"content": "B"}}]},
        ]
        body = "".join(f"data: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    responder, client = _responder(handler)
    try:
        pieces = _collect(responder)
    finally:
        asyncio.run(client.aclose())

    assert pieces == ["A", "B"]


def test_respond_stream_falls_back_to_plain_json():
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            json={"output_text": "Legacy answer"},
        )

    responder, client = _responder(handler)
    try:
        pieces = _collect(responder)
    finally:
        asyncio.run(client.aclose())

    assert pieces == ["Legacy answer"]


def test_respond_stream_raises_on_failed_event():
    def handler(request):
        body = 'data: {"type": "response.failed", "message": "boom"}\n\n'
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=body.encode(),
        )

    responder, client = _responder(handler)
    try:
        with pytest.raises(ResponderError, match="boom"):
            _collect(responder)
    finally:
        asyncio.run(client.aclose())
