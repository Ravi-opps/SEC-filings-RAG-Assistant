import asyncio
import json
from types import SimpleNamespace

import pytest

from eval_retrieval import (
    AnswerChunk,
    ChunkAssessment,
    EvalDataError,
    EvalQuestion,
    QuestionResult,
    aggregate_by_type,
    build_evidence_spans,
    first_relevant_rank,
    load_evalset,
    match_candidate,
    metrics_for,
    retrieve_reranked,
)


def test_table_row_match_accepts_value_cell_after_splitting():
    spans = build_evidence_spans(
        (
            AnswerChunk(
                "TQ04_chunk_1",
                "Cash and cash equivalents: $35,934 / $29,943; "
                "Current marketable securities: $18,763 / $35,228.",
            ),
        )
    )

    match = match_candidate(
        "35,934",
        spans,
        numeric_weight=0.75,
        threshold=0.35,
    )

    assert match is not None
    assert match.relevant is True
    assert match.matched_numbers == 1
    assert match.span_numbers == 2


def test_table_row_match_rejects_label_without_values():
    spans = build_evidence_spans(
        (
            AnswerChunk(
                "TQ04_chunk_1",
                "Cash and cash equivalents: $35,934 / $29,943.",
            ),
        )
    )

    match = match_candidate(
        "Cash and cash equivalents",
        spans,
        numeric_weight=0.75,
        threshold=0.35,
    )

    assert match is not None
    assert match.relevant is False


def test_match_rejects_unrelated_text_and_dates():
    spans = build_evidence_spans(
        (
            AnswerChunk(
                "DF01_chunk_1",
                "Total net sales: $416,161 / $391,035 / $383,285.",
            ),
        )
    )

    assert match_candidate("Table of contents", spans) is not None
    assert match_candidate("Table of contents", spans).relevant is False
    assert match_candidate("2025", spans) is None


def test_prose_match_uses_token_coverage():
    spans = build_evidence_spans(
        (
            AnswerChunk(
                "DF03_chunk_1",
                "Its reportable segments are the Americas, Europe, "
                "Greater China, Japan and Rest of Asia Pacific.",
            ),
        )
    )

    match = match_candidate(
        "The Company's reportable segments are the Americas, Europe, "
        "Greater China, Japan and Rest of Asia Pacific.",
        spans,
        threshold=0.35,
    )

    assert match is not None
    assert match.relevant is True
    assert match.token_recall == pytest.approx(1.0)


def test_first_relevant_rank_uses_reranked_order():
    assessments = tuple(
        ChunkAssessment(
            rank=rank,
            point_id=f"p{rank}",
            vector_score=0.5,
            rerank_score=0.9,
            relevant=rank == 3,
            evidence=None,
            unit_type="paragraph",
            section="",
            text="",
        )
        for rank in range(1, 6)
    )

    assert first_relevant_rank(assessments) == 3
    assert first_relevant_rank(assessments[:2]) is None


def _result(
    question_id: str,
    question_type: str,
    reciprocal_rank: float,
    rank: int | None,
    error: str | None = None,
) -> QuestionResult:
    return QuestionResult(
        id=question_id,
        question_type=question_type,
        question="question",
        reference_answer="answer",
        rank=rank,
        reciprocal_rank=reciprocal_rank,
        best_score=0.5,
        error=error,
    )


def test_metrics_exclude_failures_and_count_no_matches():
    results = [
        _result("q1", "Direct Fact", 1.0, 1),
        _result("q2", "Direct Fact", 0.5, 2),
        _result("q3", "Temporal Q", 0.0, None),
        _result("q4", "Temporal Q", 0.0, None, error="boom"),
    ]

    overall = metrics_for(results)
    by_type = aggregate_by_type(results)

    assert overall.count == 3
    assert overall.failed == 1
    assert overall.mrr == pytest.approx(0.5)
    assert overall.hit_at_1 == pytest.approx(1 / 3)
    assert overall.hit_at_3 == pytest.approx(2 / 3)
    assert overall.no_match == 1
    assert by_type["Direct Fact"].mrr == pytest.approx(0.75)
    assert by_type["Temporal Q"].count == 1
    assert by_type["Temporal Q"].failed == 1


def test_load_evalset_reads_questions(tmp_path):
    path = tmp_path / "evalset.jsonl"
    path.write_text(
        json.dumps(
            {
                "id": "DF01",
                "question_type": "Direct Fact",
                "question": "What was total net sales?",
                "reference_answer": "$416,161 million.",
                "answer_chunks": [
                    {"chunk_id": "DF01_chunk_1", "text": "Total net sales: 416,161."}
                ],
                "source_document": "Apple 10-K",
                "source_pages": [21, "22"],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    questions = load_evalset(path)

    assert len(questions) == 1
    assert questions[0].id == "DF01"
    assert questions[0].source_pages == (21, 22)
    assert questions[0].answer_chunks[0].chunk_id == "DF01_chunk_1"


def test_load_evalset_rejects_duplicate_ids(tmp_path):
    row = {
        "id": "DF01",
        "question_type": "Direct Fact",
        "question": "question",
        "answer_chunks": [{"text": "answer text"}],
    }
    path = tmp_path / "evalset.jsonl"
    path.write_text(
        json.dumps(row) + "\n" + json.dumps(row) + "\n",
        encoding="utf-8",
    )

    with pytest.raises(EvalDataError):
        load_evalset(path)


def test_build_evidence_spans_requires_usable_answer_text():
    with pytest.raises(EvalDataError):
        build_evidence_spans((AnswerChunk("x", "a"),))


def test_retrieve_reranked_applies_rerank_order_to_chunk_text():
    class Embedder:
        async def embed_query(self, question):
            return [0.1]

    class Store:
        async def search(self, vector, *, limit, document_id):
            return [
                SimpleNamespace(
                    point_id="p1",
                    score=0.9,
                    payload={
                        "text": "first chunk",
                        "embedding_text": "first chunk",
                        "unit_type": "paragraph",
                    },
                ),
                SimpleNamespace(
                    point_id="p2",
                    score=0.8,
                    payload={
                        "text": "second chunk",
                        "embedding_text": "second chunk",
                        "unit_type": "paragraph",
                    },
                ),
            ]

    class Reranker:
        async def rerank(self, question, texts, *, top_n):
            assert texts == ["first chunk", "second chunk"]
            assert top_n == 2
            return [(1, 0.7), (0, 0.6)]

    chunks = asyncio.run(
        retrieve_reranked(
            "question",
            embedder=Embedder(),
            store=Store(),
            reranker=Reranker(),
            top_k=20,
            final_k=2,
            document_id="doc",
        )
    )

    assert [chunk.point_id for chunk in chunks] == ["p2", "p1"]
    assert [chunk.text for chunk in chunks] == ["second chunk", "first chunk"]
    assert chunks[0].rerank_score == 0.7
