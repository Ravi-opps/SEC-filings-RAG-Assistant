import asyncio

from langchain_core.messages import SystemMessage

from input_guardrail import GuardDecision
from retrieval_agent import (
    AgentRuntime,
    AgentState,
    PlannerDecision,
    build_agent_graph,
)


def _state(memory_context: str) -> AgentState:
    return {
        "current_msg": "What do you remember about me?",
        "conversation": [],
        "memory_context": memory_context,
        "plan": {},
        "status": "planning",
        "final_ans": "",
        "guardrail": {},
        "retrieval_query": "",
        "retrieved_context": "",
        "sources": [],
    }


def test_core_agent_injects_memory_into_planner_and_responder():
    seen = {}

    def planner(prompt, history):
        seen["planner"] = history
        return PlannerDecision(type="conversational")

    def conversationalist(prompt, history, context):
        seen["responder"] = history
        return "You prefer concise answers."

    graph = build_agent_graph(planner, conversationalist)
    result = asyncio.run(
        graph.ainvoke(
            _state("Long-term memories:\n- User prefers concise answers.")
        )
    )

    assert result["final_ans"] == "You prefer concise answers."
    for history in (seen["planner"], seen["responder"]):
        assert any(
            isinstance(message, SystemMessage)
            and "User prefers concise answers." in message.content
            for message in history
        )


def test_agent_runtime_plumbs_memory_context_into_graph():
    seen = {}

    def planner(prompt, history):
        seen["planner"] = history
        return PlannerDecision(type="conversational")

    def conversationalist(prompt, history, context):
        seen["responder"] = history
        return "Remembered."

    def guardrail(prompt, history):
        return GuardDecision(
            allowed=True,
            category="test",
            reason="test",
        )

    async def retriever(query):
        return []

    runtime = AgentRuntime(
        planner=planner,
        conversationalist=conversationalist,
        retriever=retriever,
        guardrail=guardrail,
    )
    result = asyncio.run(
        runtime.run(
            "hello",
            memory_context="Long-term memories:\n- User prefers concise answers.",
        )
    )

    assert result["final_ans"] == "Remembered."
    for history in (seen["planner"], seen["responder"]):
        assert any(
            isinstance(message, SystemMessage)
            and "User prefers concise answers." in message.content
            for message in history
        )


def test_core_agent_injects_memory_into_retrieval_rewriter():
    seen = {}

    def planner(prompt, history):
        return PlannerDecision(type="retrieval")

    def rewriter(prompt, history):
        seen["rewriter"] = history
        return "memory test query"

    async def retriever(query):
        return []

    def conversationalist(prompt, history, context):
        seen["responder"] = history
        return "No filing context found."

    graph = build_agent_graph(
        planner,
        conversationalist,
        rewriter=rewriter,
        retriever=retriever,
    )
    result = asyncio.run(
        graph.ainvoke(
            _state("Long-term memories:\n- User covers AAPL.")
        )
    )

    assert result["final_ans"] == "No filing context found."
    for history in (seen["rewriter"], seen["responder"]):
        assert any(
            isinstance(message, SystemMessage)
            and "User covers AAPL." in message.content
            for message in history
        )
