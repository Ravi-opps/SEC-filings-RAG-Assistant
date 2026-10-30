"""LangGraph routing layer for the SEC-filing retrieval pipeline."""

from __future__ import annotations

import asyncio
import inspect
import json
import os
from typing import (
    Annotated,
    Any,
    AsyncIterator,
    Awaitable,
    Callable,
    Literal,
    Optional,
    TypedDict,
)

import httpx
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolMessage,
    convert_to_messages,
)
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.graph import END, StateGraph
from langgraph.graph.message import add_messages
from pydantic import BaseModel, ValidationError

from input_guardrail import (
    REFUSAL_MESSAGE,
    GuardDecision,
    GuardrailError,
    InputGuardrail,
)
from retrieval_service import (
    RetrievalConfigError,
    RetrievalError,
    RetrievalService,
    RetrievedChunk,
    build_sources,
    format_retrieved_context,
)

DEFAULT_CLASSIFIER_MODEL = "gpt-5.4-pro"
DEFAULT_CONVERSATIONAL_MODEL = "gpt-6-sol"

Route = Literal["conversational", "retrieval"]


class PlannerDecision(BaseModel):
    type: Route


class AgentState(TypedDict):
    current_msg: str
    conversation: Annotated[list[BaseMessage], add_messages]
    plan: dict[str, Any]
    status: str
    final_ans: str
    guardrail: dict[str, Any]
    retrieval_query: str
    retrieved_context: str
    sources: list[dict[str, Any]]


class PlannerError(Exception):
    """Raised when the routing model fails or returns an unusable response."""


class PlannerConfigError(PlannerError):
    """Raised when the routing model endpoint or credentials are missing."""


class ResponderError(Exception):
    """Raised when the conversational model fails or returns no answer."""


History = list[BaseMessage] | list[dict[str, Any]]

PlannerFn = Callable[
    [str, History],
    Awaitable[PlannerDecision] | PlannerDecision,
]

ResponderFn = Callable[
    [str, History, str | None],
    Awaitable[str] | str,
]

AnswerStreamerFn = Callable[
    [str, History, str | None],
    AsyncIterator[str],
]

RewriterFn = Callable[[str, History], Awaitable[str] | str]

RetrieverFn = Callable[[str], Awaitable[list[RetrievedChunk]]]

GuardFn = Callable[
    [str, History],
    Awaitable[GuardDecision] | GuardDecision,
]


PLANNER_SYSTEM_PROMPT = """You are the routing planner for a SEC filing retrieval assistant.
You do not answer the user. You only decide how the latest user message should be handled.

The assistant may state company facts only when they come from indexed SEC filing context.
Your own knowledge of companies, financials, or current events is not a permitted source.
Never route a request for company or filing facts to "conversational" just because the answer
is well known, seems obvious, or you could answer it without searching.

Choose "retrieval" when the latest message:
- asks about a company, SEC filing, financial statement, financial metric, risk factor, MD&A,
  or any other filing content;
- asks for any fact, figure, date, quote, comparison, or analysis about a public company,
  including revenue, income, profit, earnings, expenses, margins, segments, employees,
  guidance, litigation, or executive compensation;
- asks a follow-up that needs filing facts not already stated in the conversation;
- is ambiguous about whether filing evidence is needed. Default to retrieval when unsure.

Choose "conversational" only when the latest message:
- is a greeting, thanks, farewell, or small talk;
- asks about the assistant's capabilities or how to use it;
- is a clarification or acknowledgement that requests no new factual answer;
- asks about an answer or retrieved context already present in the conversation history,
  without requesting new facts;
- repeats a question whose answer is already present in the conversation history and that
  answer was grounded in filing context.

Do not choose "conversational" merely because the conversation history exists or because you
believe you know the answer from training data. If the latest message requests company facts
and the grounding is not already in the conversation, return "retrieval".
Return only one JSON object, with exactly one key named "type".
Allowed values: "conversational" or "retrieval".
"""


CONVERSATIONAL_SYSTEM_PROMPT = """You are the grounded answer assistant for a SEC filing research system.
You may use only these sources:
1. the retrieved filing context provided in this prompt;
2. filing facts already established in the conversation history, including prior retrieved context.

You must not use your own pretrained knowledge about companies, financials, markets, or events.
Every factual claim about a company must be supported by the retrieved filing context or by
filing facts already present in the conversation history.

Rules:
- Never answer a company or filing factual question from memory, even if you are confident.
- Do not guess, estimate, or fill gaps with plausible numbers, dates, or figures.
- When retrieved filing context is present, answer only from it and cite the numbered chunks
  you use, for example [1], [2].
- If the needed facts are not present in the retrieved context or the conversation history,
  say that you could not find the answer in the indexed filings and suggest retrieving or
  indexing the relevant filing. Do not answer from general knowledge.
- Greetings, thanks, capability questions, and clarifications that request no filing facts may
  be answered naturally without citations.
- Be concise and helpful.
"""

QUERY_REWRITE_SYSTEM_PROMPT = """You rewrite the latest user message into a self-sufficient search query
for a SEC filing vector index.

Use the conversation history to resolve pronouns, demonstratives, ellipsis, and
vague references. Replace them with the actual company names, years, people,
metrics, filing sections, or other nouns they refer to. Make implicit
relationships explicit instead of leaving ambiguous prepositions.

Do not answer the question. Do not add facts that are not present in the
conversation history. Preserve the user's intent and scope.
Return only the rewritten query as plain text, with no quotes, labels, or
explanation.
"""


def responses_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/responses"):
        return base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return f"{base}/responses"


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
        return " ".join(parts).strip()
    return str(content).strip()


def _message_role(message: BaseMessage) -> str:
    if isinstance(message, ToolMessage):
        return f"tool[{message.name or 'tool'}]"
    return message.type


def format_conversation(conversation: History | None) -> str:
    if not conversation:
        return "(none)"
    lines: list[str] = []
    for message in convert_to_messages(conversation):
        content = _content_text(message.content)
        if content:
            lines.append(f"{_message_role(message)}: {content}")
    return "\n".join(lines) if lines else "(none)"


def build_planner_prompt(
    current_msg: str,
    conversation: History | None,
) -> str:
    history = format_conversation(conversation)
    return f"""Conversation history (oldest first):
{history}

Latest user message:
{current_msg}

Return only JSON in one of these two forms:
{{"type": "conversational"}}
{{"type": "retrieval"}}
"""


def build_conversational_prompt(
    current_msg: str,
    conversation: History | None,
    retrieved_context: str | None = None,
) -> str:
    history = format_conversation(conversation)
    if retrieved_context:
        context_block = f"""
Retrieved filing context:
{retrieved_context}
"""
    else:
        context_block = """
Retrieved filing context: none for this turn.
"""
    return f"""Conversation history (oldest first):
{history}
{context_block}
Latest user message:
{current_msg}

Respond to the latest user message.
Answer company or filing factual questions only from the retrieved filing context or from filing
facts already present in the conversation history. If the required facts are not there, say you
could not find the answer in the indexed filings. Do not answer from general knowledge.
"""


def build_rewrite_prompt(
    current_msg: str,
    conversation: History | None,
) -> str:
    history = format_conversation(conversation)
    return f"""Conversation history (oldest first):
{history}

Latest user message:
{current_msg}

Rewrite the latest user message as a self-sufficient SEC filing search query.
"""


def normalize_route(value: Any) -> Route | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower().replace("_", "-")
    if normalized in {
        "conversation",
        "conversational",
        "chat",
        "chit-chat",
        "chitchat",
        "small-talk",
    }:
        return "conversational"
    if normalized in {"retrieval", "retrievable", "retrieve"}:
        return "retrieval"
    return None


def _strip_code_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if lines:
        lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def parse_planner_response(content: str) -> PlannerDecision:
    text = _strip_code_fence(content)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        raise PlannerError("planner response did not contain a JSON object")
    try:
        payload = json.loads(text[start : end + 1])
    except (TypeError, ValueError) as exc:
        raise PlannerError("planner response was not valid JSON") from exc
    if not isinstance(payload, dict):
        raise PlannerError("planner response was not a JSON object")
    route = normalize_route(payload.get("type"))
    if route is None:
        raise PlannerError("planner response did not contain a valid route")
    try:
        return PlannerDecision(type=route)
    except ValidationError as exc:
        raise PlannerError("planner response failed validation") from exc


def _response_content(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise PlannerError("planner response was not a JSON object")

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text

    output = payload.get("output")
    if not isinstance(output, list):
        raise PlannerError("planner response did not contain output")
    parts: list[str] = []
    for item in output:
        if not isinstance(item, dict):
            continue
        content = item.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "output_text" and isinstance(
                block.get("text"), str
            ):
                parts.append(block["text"])
    if parts:
        return "".join(parts)
    raise PlannerError("planner response output was empty")


class LLMPlanner:
    """Azure OpenAI Responses client used by the planner node."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_url = url or os.getenv("CLASSIFIER_URL") or ""
        resolved_key = api_key or os.getenv("CLASSIFIER_KEY") or ""
        resolved_model = (
            model
            or os.getenv("CLASSIFIER_NAME")
            or DEFAULT_CLASSIFIER_MODEL
        )
        if not resolved_url:
            raise PlannerConfigError("CLASSIFIER_URL is not configured")
        if not resolved_key:
            raise PlannerConfigError("CLASSIFIER_KEY is not configured")
        if not resolved_model:
            raise PlannerConfigError("CLASSIFIER_NAME is not configured")
        self.url = responses_url(resolved_url)
        self.api_key = resolved_key
        self.model = resolved_model
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def _complete(self, instructions: str, user_input: str) -> str:
        body = {
            "model": self.model,
            "instructions": instructions,
            "input": user_input,
        }
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        try:
            response = await self.client.post(
                self.url,
                headers=headers,
                json=body,
            )
        except httpx.HTTPError as exc:
            raise PlannerError(f"classifier request failed: {exc}") from exc
        if response.status_code >= 400:
            raise PlannerError(
                f"classifier request failed with status {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise PlannerError("classifier response was not JSON") from exc
        return _response_content(payload)

    async def classify(
        self,
        current_msg: str,
        conversation: History | None,
    ) -> PlannerDecision:
        content = await self._complete(
            PLANNER_SYSTEM_PROMPT,
            build_planner_prompt(current_msg, conversation),
        )
        return parse_planner_response(content)

    async def rewrite_query(
        self,
        current_msg: str,
        conversation: History | None,
    ) -> str:
        content = await self._complete(
            QUERY_REWRITE_SYSTEM_PROMPT,
            build_rewrite_prompt(current_msg, conversation),
        )
        query = _strip_code_fence(content).strip().strip("\"'").strip()
        if not query:
            raise PlannerError("classifier returned an empty retrieval query")
        return query

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None


class LLMResponder:
    """Azure OpenAI Responses client for conversational replies."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 180.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        resolved_url = url or os.getenv("SOL_URL") or ""
        self.api_key = api_key or os.getenv("OPENAI_SOL") or ""
        self.model = model or DEFAULT_CONVERSATIONAL_MODEL
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        if not resolved_url:
            raise PlannerConfigError("SOL_URL is not configured")
        if not self.api_key:
            raise PlannerConfigError("OPENAI_SOL is not configured")
        self.url = responses_url(resolved_url)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def _body(
        self,
        current_msg: str,
        conversation: History | None,
        retrieved_context: str | None,
        *,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": CONVERSATIONAL_SYSTEM_PROMPT,
            "input": build_conversational_prompt(
                current_msg,
                conversation,
                retrieved_context,
            ),
        }
        if stream:
            body["stream"] = True
        return body

    def _headers(self, *, stream: bool = False) -> dict[str, str]:
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        return headers

    async def respond(
        self,
        current_msg: str,
        conversation: History | None,
        retrieved_context: str | None = None,
    ) -> str:
        body = self._body(
            current_msg,
            conversation,
            retrieved_context,
            stream=False,
        )
        try:
            response = await self.client.post(
                self.url,
                headers=self._headers(),
                json=body,
            )
        except httpx.HTTPError as exc:
            raise ResponderError(f"conversational request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ResponderError(
                "conversational request failed with "
                f"status {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ResponderError("conversational response was not JSON") from exc
        answer = _response_content(payload).strip()
        if not answer:
            raise ResponderError("conversational response was empty")
        return answer

    async def respond_stream(
        self,
        current_msg: str,
        conversation: History | None,
        retrieved_context: str | None = None,
    ) -> AsyncIterator[str]:
        body = self._body(
            current_msg,
            conversation,
            retrieved_context,
            stream=True,
        )
        headers = self._headers(stream=True)
        try:
            async with self.client.stream(
                "POST",
                self.url,
                headers=headers,
                json=body,
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise ResponderError(
                        "conversational request failed with "
                        f"status {response.status_code}"
                    )

                content_type = response.headers.get("content-type", "").lower()
                if "text/event-stream" not in content_type:
                    raw = await response.aread()
                    try:
                        payload = json.loads(raw)
                    except (TypeError, ValueError) as exc:
                        raise ResponderError(
                            "conversational response was not JSON"
                        ) from exc
                    answer = _response_content(payload).strip()
                    if not answer:
                        raise ResponderError("conversational response was empty")
                    yield answer
                    return

                emitted = False
                done_text: str | None = None
                async for line in response.aiter_lines():
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:") :].strip()
                    if not data or data == "[DONE]":
                        continue
                    try:
                        payload = json.loads(data)
                    except (TypeError, ValueError):
                        continue
                    if not isinstance(payload, dict):
                        continue

                    event_type = payload.get("type")
                    if event_type == "response.output_text.delta":
                        delta = payload.get("delta")
                        if isinstance(delta, str) and delta:
                            emitted = True
                            yield delta
                        continue
                    if event_type == "response.output_text.done":
                        text = payload.get("text")
                        if isinstance(text, str) and text.strip():
                            done_text = text
                        continue
                    if event_type in {"response.failed", "error"}:
                        detail = payload.get("message")
                        if not isinstance(detail, str) or not detail.strip():
                            detail = "conversational stream failed"
                        raise ResponderError(detail)
                    if event_type == "response.completed":
                        if not emitted and not done_text:
                            completed = payload.get("response")
                            if isinstance(completed, dict):
                                try:
                                    done_text = _response_content(completed)
                                except PlannerError:
                                    done_text = None
                        continue

                    choices = payload.get("choices")
                    if isinstance(choices, list) and choices:
                        choice = choices[0]
                        delta = choice.get("delta") if isinstance(choice, dict) else None
                        if isinstance(delta, dict):
                            text = delta.get("content")
                            if isinstance(text, str) and text:
                                emitted = True
                                yield text

                if not emitted and done_text:
                    emitted = True
                    yield done_text
                if not emitted:
                    raise ResponderError("conversational response was empty")
        except httpx.HTTPError as exc:
            raise ResponderError(
                f"conversational stream failed: {exc}"
            ) from exc

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None


def _coerce_decision(value: PlannerDecision | dict[str, Any] | str) -> PlannerDecision:
    if isinstance(value, PlannerDecision):
        return value
    if isinstance(value, str):
        route = normalize_route(value)
        if route is not None:
            return PlannerDecision(type=route)
    if isinstance(value, dict):
        route = normalize_route(value.get("type"))
        if route is not None:
            return PlannerDecision(type=route)
    raise PlannerError("planner returned an invalid decision")


async def _identity_rewrite(
    current_msg: str,
    _conversation: History | None,
) -> str:
    return current_msg


async def _allow_all_guard(
    _current_msg: str,
    _conversation: History | None,
) -> GuardDecision:
    return GuardDecision(
        allowed=True,
        category="guardrail_disabled",
        reason="input guardrail is disabled",
    )


def _coerce_guard_decision(
    value: GuardDecision | dict[str, Any] | bool,
) -> GuardDecision:
    if isinstance(value, GuardDecision):
        return value
    if isinstance(value, bool):
        return GuardDecision(
            allowed=value,
            category="guardrail_result",
            reason="guardrail returned a boolean",
        )
    if isinstance(value, dict):
        try:
            return GuardDecision.model_validate(value)
        except ValidationError as exc:
            raise GuardrailError("guardrail returned an invalid decision") from exc
    raise GuardrailError("guardrail returned an invalid decision")


def _stream_writer(
    config: Optional[RunnableConfig],
) -> Callable[[Any], None] | None:
    configurable = (config or {}).get("configurable") or {}
    if not configurable.get("stream"):
        return None
    try:
        return get_stream_writer()
    except Exception:
        return None


def build_agent_graph(
    planner: PlannerFn,
    conversationalist: ResponderFn,
    rewriter: RewriterFn | None = None,
    retriever: RetrieverFn | None = None,
    guardrail: GuardFn | None = None,
    answer_streamer: AnswerStreamerFn | None = None,
):
    rewrite = rewriter or _identity_rewrite
    guard = guardrail or _allow_all_guard

    async def guardrail_node(
        state: AgentState,
        config: Optional[RunnableConfig] = None,
    ) -> dict[str, Any]:
        writer = _stream_writer(config)
        if writer is not None:
            writer({"event": "status", "data": {"stage": "guardrail"}})
        try:
            result = guard(
                state.get("current_msg", ""),
                list(state.get("conversation") or []),
            )
            if inspect.isawaitable(result):
                result = await result
            decision = _coerce_guard_decision(result)
        except GuardrailError:
            raise
        except Exception as exc:
            raise GuardrailError(f"input guardrail failed: {exc}") from exc

        if not decision.allowed:
            return {
                "guardrail": decision.model_dump(),
                "plan": {"type": "blocked"},
                "status": "blocked",
                "final_ans": REFUSAL_MESSAGE,
                "conversation": [
                    HumanMessage(content=state.get("current_msg", "")),
                    AIMessage(content=REFUSAL_MESSAGE),
                ],
            }
        return {
            "guardrail": decision.model_dump(),
            "status": "allowed",
        }

    async def planner_node(
        state: AgentState,
        config: Optional[RunnableConfig] = None,
    ) -> dict[str, Any]:
        writer = _stream_writer(config)
        if writer is not None:
            writer({"event": "status", "data": {"stage": "planning"}})
        try:
            result = planner(
                state.get("current_msg", ""),
                list(state.get("conversation") or []),
            )
            if inspect.isawaitable(result):
                result = await result
            decision = _coerce_decision(result)
        except PlannerError:
            raise
        except Exception as exc:
            raise PlannerError(f"planner failed: {exc}") from exc
        return {
            "plan": {"type": decision.type},
            "status": "planned",
        }

    async def conversational_node(
        state: AgentState,
        config: Optional[RunnableConfig] = None,
    ) -> dict[str, Any]:
        history = list(state.get("conversation") or [])
        retrieved = state.get("status") == "retrieved"
        if retrieved:
            history = history[:-2]
        writer = _stream_writer(config)
        if writer is not None:
            writer({"event": "status", "data": {"stage": "answering"}})
        try:
            if writer is not None and answer_streamer is not None:
                parts: list[str] = []
                stream = answer_streamer(
                    state.get("current_msg", ""),
                    history,
                    state.get("retrieved_context") or None,
                )
                async for piece in stream:
                    if not piece:
                        continue
                    parts.append(piece)
                    writer({"event": "delta", "data": {"text": piece}})
                answer = "".join(parts).strip()
            else:
                result = conversationalist(
                    state.get("current_msg", ""),
                    history,
                    state.get("retrieved_context") or None,
                )
                if inspect.isawaitable(result):
                    result = await result
                answer = str(result or "").strip()
        except ResponderError:
            raise
        except Exception as exc:
            raise ResponderError(f"conversational responder failed: {exc}") from exc
        if not answer:
            raise ResponderError("conversational responder returned an empty answer")
        if state.get("status") == "retrieved":
            messages: list[BaseMessage] = [AIMessage(content=answer)]
        else:
            messages = [
                HumanMessage(content=state.get("current_msg", "")),
                AIMessage(content=answer),
            ]
        return {
            "conversation": messages,
            "status": "conversational",
            "final_ans": answer,
        }

    async def retrieval_node(
        state: AgentState,
        config: Optional[RunnableConfig] = None,
    ) -> dict[str, Any]:
        current_msg = state.get("current_msg", "")
        conversation = list(state.get("conversation") or [])
        if retriever is None:
            raise RetrievalConfigError("retrieval service is not configured")
        writer = _stream_writer(config)
        if writer is not None:
            writer({"event": "status", "data": {"stage": "retrieving"}})
        try:
            rewritten = rewrite(current_msg, conversation)
            if inspect.isawaitable(rewritten):
                rewritten = await rewritten
            query = str(rewritten or "").strip()
            if not query:
                raise PlannerError("query rewrite returned an empty query")
            chunks = await retriever(query)
        except (PlannerError, RetrievalError):
            raise
        except Exception as exc:
            raise RetrievalError(f"retrieval failed: {exc}") from exc

        context = format_retrieved_context(chunks)
        sources = build_sources(chunks)
        messages: list[BaseMessage] = [
            HumanMessage(content=current_msg),
            ToolMessage(
                content=context or "No relevant SEC filing content was retrieved.",
                tool_call_id="retrieval",
            ),
        ]
        if writer is not None:
            writer({"event": "sources", "data": sources})
        return {
            "retrieval_query": query,
            "retrieved_context": context,
            "sources": sources,
            "conversation": messages,
            "status": "retrieved",
        }

    def guard_route(state: AgentState) -> str:
        return "blocked" if state.get("status") == "blocked" else "allowed"

    def route(state: AgentState) -> str:
        return str(state.get("plan", {}).get("type") or "")

    builder = StateGraph(AgentState)
    builder.add_node("guardrail", guardrail_node)
    builder.add_node("planner", planner_node)
    builder.add_node("conversational", conversational_node)
    builder.add_node("retrieval", retrieval_node)
    builder.set_entry_point("guardrail")
    builder.add_conditional_edges(
        "guardrail",
        guard_route,
        {
            "blocked": END,
            "allowed": "planner",
        },
    )
    builder.add_conditional_edges(
        "planner",
        route,
        {
            "conversational": "conversational",
            "retrieval": "retrieval",
        },
    )
    builder.add_edge("retrieval", "conversational")
    builder.add_edge("conversational", END)
    return builder.compile()


class AgentRuntime:
    def __init__(
        self,
        planner: PlannerFn | None = None,
        *,
        llm: LLMPlanner | None = None,
        conversationalist: ResponderFn | None = None,
        conversational_llm: LLMResponder | None = None,
        rewriter: RewriterFn | None = None,
        retriever: RetrieverFn | None = None,
        retrieval_service: RetrievalService | None = None,
        guardrail: GuardFn | None = None,
        input_guardrail: InputGuardrail | None = None,
    ) -> None:
        self._owns_guardrail = guardrail is None and input_guardrail is None
        if guardrail is None:
            self.input_guardrail = input_guardrail or InputGuardrail()
            guardrail = self.input_guardrail.check
        else:
            self.input_guardrail = input_guardrail

        self.llm = llm if planner is None else None
        if planner is None:
            self.llm = self.llm or LLMPlanner()
            planner = self.llm.classify

        self.conversational_llm = (
            conversational_llm if conversationalist is None else None
        )
        if conversationalist is None:
            self.conversational_llm = (
                self.conversational_llm or LLMResponder()
            )
            conversationalist = self.conversational_llm.respond
        self.conversational_streamer = (
            self.conversational_llm.respond_stream
            if self.conversational_llm is not None
            else None
        )

        self._owns_retrieval = retriever is None and retrieval_service is None
        if retriever is None:
            self.retrieval_service = retrieval_service or RetrievalService()
            retriever = self.retrieval_service.retrieve
        else:
            self.retrieval_service = retrieval_service

        self.rewriter = rewriter
        if self.rewriter is None and self.llm is not None:
            self.rewriter = self.llm.rewrite_query
        self.graph = build_agent_graph(
            planner,
            conversationalist,
            self.rewriter,
            retriever,
            guardrail,
            self.conversational_streamer,
        )
        self._conversations: dict[str, list[BaseMessage]] = {}
        self._conversation_locks: dict[str, asyncio.Lock] = {}

    def _conversation_lock(self, conversation_id: str) -> asyncio.Lock:
        lock = self._conversation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[conversation_id] = lock
        return lock

    async def _invoke(
        self,
        user_prompt: str,
        conversation: History | None,
    ) -> AgentState:
        state: AgentState = {
            "current_msg": user_prompt,
            "conversation": convert_to_messages(conversation or []),
            "plan": {},
            "status": "planning",
            "final_ans": "",
            "guardrail": {},
            "retrieval_query": "",
            "retrieved_context": "",
            "sources": [],
        }
        return await self.graph.ainvoke(state)

    async def _invoke_stream(
        self,
        user_prompt: str,
        conversation: History | None,
        conversation_id: str | None,
    ) -> AsyncIterator[dict[str, Any]]:
        state: AgentState = {
            "current_msg": user_prompt,
            "conversation": convert_to_messages(conversation or []),
            "plan": {},
            "status": "planning",
            "final_ans": "",
            "guardrail": {},
            "retrieval_query": "",
            "retrieved_context": "",
            "sources": [],
        }
        final_state: AgentState | None = None
        async for mode, chunk in self.graph.astream(
            state,
            stream_mode=["values", "custom"],
            config={"configurable": {"stream": True}},
        ):
            if mode == "custom":
                yield chunk
            elif mode == "values":
                final_state = chunk

        if final_state is None:
            raise ResponderError("agent stream finished without a final state")
        if conversation_id is not None:
            self._conversations[conversation_id] = list(
                final_state.get("conversation") or []
            )
        route = final_state.get("plan", {}).get("type")
        if route not in ("conversational", "retrieval", "blocked"):
            raise PlannerError("planner did not return a valid route")
        yield {
            "event": "done",
            "data": {
                "type": route,
                "answer": final_state.get("final_ans") or None,
                "sources": final_state.get("sources") or [],
            },
        }

    async def run(
        self,
        user_prompt: str,
        conversation: History | None = None,
        *,
        conversation_id: str | None = None,
    ) -> AgentState:
        conversation_id = (conversation_id or "").strip() or None
        if conversation_id is None:
            return await self._invoke(user_prompt, conversation)

        async with self._conversation_lock(conversation_id):
            if conversation is None:
                history = list(self._conversations.get(conversation_id, []))
            else:
                history = conversation
            result = await self._invoke(user_prompt, history)
            self._conversations[conversation_id] = list(
                result.get("conversation") or []
            )
            return result

    async def run_stream(
        self,
        user_prompt: str,
        conversation: History | None = None,
        *,
        conversation_id: str | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        conversation_id = (conversation_id or "").strip() or None
        if conversation_id is None:
            async for item in self._invoke_stream(
                user_prompt,
                conversation,
                None,
            ):
                yield item
            return

        async with self._conversation_lock(conversation_id):
            if conversation is None:
                history = list(self._conversations.get(conversation_id, []))
            else:
                history = conversation
            async for item in self._invoke_stream(
                user_prompt,
                history,
                conversation_id,
            ):
                yield item

    async def close(self) -> None:
        if self._owns_guardrail and self.input_guardrail is not None:
            await self.input_guardrail.close()
        if self._owns_retrieval and self.retrieval_service is not None:
            await self.retrieval_service.close()
        if self.conversational_llm is not None:
            await self.conversational_llm.close()
        if self.llm is not None:
            await self.llm.close()


_runtime: AgentRuntime | None = None


def get_agent_runtime() -> AgentRuntime:
    global _runtime
    if _runtime is None:
        _runtime = AgentRuntime()
    return _runtime


async def run_agent(
    user_prompt: str,
    conversation: History | None = None,
    *,
    conversation_id: str | None = None,
) -> AgentState:
    prompt = user_prompt.strip()
    if not prompt:
        raise ValueError("user prompt cannot be empty")
    return await get_agent_runtime().run(
        prompt,
        conversation,
        conversation_id=conversation_id,
    )


async def stream_agent(
    user_prompt: str,
    conversation: History | None = None,
    *,
    conversation_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    prompt = user_prompt.strip()
    if not prompt:
        raise ValueError("user prompt cannot be empty")
    async for item in get_agent_runtime().run_stream(
        prompt,
        conversation,
        conversation_id=conversation_id,
    ):
        yield item


async def close_agent_runtime() -> None:
    global _runtime
    if _runtime is not None:
        await _runtime.close()
        _runtime = None
