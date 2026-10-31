import pytest

from eval_keyword_coverage import (
    Keyword,
    KeywordResult,
    aggregate_by_type,
    build_expected_keywords,
    extract_keywords,
    metrics_for,
    score_keywords,
)
from eval_retrieval import AnswerChunk, EvalQuestion
from retrieval_service import RetrievedChunk


def _question(
    reference_answer: str,
    answer_text: str,
    *,
    question_id: str = "TQ01",
    question_type: str = "Temporal Q",
) -> EvalQuestion:
    return EvalQuestion(
        id=question_id,
        question_type=question_type,
        question="question",
        reference_answer=reference_answer,
        answer_chunks=(AnswerChunk("chunk-1", answer_text),),
        source_document="Apple 10-K",
        source_pages=(),
    )


def _chunk(text: str, point_id: str = "p1") -> RetrievedChunk:
    return RetrievedChunk(
        point_id=point_id,
        text=text,
        vector_score=0.5,
        rerank_score=0.9,
        payload={},
    )


def test_extract_keywords_normalizes_scaled_numbers_and_skips_years():
    keywords = extract_keywords(
        "Total net sales were $416.161 billion in 2025; "
        "cash was $35,934 million."
    )
    keys = {keyword.key for keyword in keywords}

    assert "num:416161" in keys
    assert "num:35934" in keys
    assert "num:2025" not in keys
    assert "token:sale" in keys
    assert "token:total" in keys


def test_extract_keywords_normalizes_percentages():
    keys = {
        keyword.key
        for keyword in extract_keywords("60% and 60.0 percent.")
    }

    assert keys == {"pct:60"}


def test_grounded_reference_drops_derived_values_not_in_source():
    question = _question(
        "Greater China net sales declined from $72.559 billion in 2023 "
        "to $64.377 billion in 2025, a decrease of $8.182 billion "
        "(about 11.3%).",
        "Net sales by reportable segment: "
        "Greater China: $64,377 / $66,952 / $72,559.",
    )

    expected = build_expected_keywords(question, "grounded-reference")
    keys = {keyword.key for keyword in expected}

    assert "num:72559" in keys
    assert "num:64377" in keys
    assert "num:8182" not in keys
    assert "pct:11.3" not in keys
    assert "token:greater" in keys
    assert "token:china" in keys
    assert "token:sale" in keys


def test_score_keywords_uses_union_of_reranked_chunks():
    question = _question(
        "Net sales were $416.161 billion.",
        "Total net sales: $416,161.",
    )
    expected = (
        Keyword("token:net", "net"),
        Keyword("token:sale", "sale"),
        Keyword("num:416161", "$416.161 billion"),
    )
    chunks = (
        _chunk("iPhone sales were $209,586.", "p1"),
        _chunk("Total net sales were $416,161.", "p2"),
    )

    result = score_keywords(question, expected, chunks)

    assert result.coverage == pytest.approx(1.0)
    assert result.top1_coverage == pytest.approx(1 / 3)
    assert result.all_covered is True
    assert result.missing_keywords == ()
    assert [chunk.rank for chunk in result.chunks] == [1, 2]


def test_score_keywords_reports_missing_terms():
    question = _question("answer", "answer")
    expected = (
        Keyword("token:cash", "cash"),
        Keyword("num:35934", "$35.934 billion"),
    )

    result = score_keywords(
        question,
        expected,
        (_chunk("iPhone sales were $209,586."),),
    )

    assert result.coverage == 0.0
    assert result.top1_coverage == 0.0
    assert result.all_covered is False
    assert result.missing_keywords == ("cash", "$35.934 billion")


def test_metrics_excludes_errors_and_uses_micro_coverage():
    results = [
        KeywordResult(
            id="q1",
            question_type="Direct Fact",
            question="q",
            expected_keywords=("a", "b"),
            matched_keywords=("a",),
            missing_keywords=("b",),
            coverage=0.5,
            top1_coverage=0.5,
            all_covered=False,
        ),
        KeywordResult(
            id="q2",
            question_type="Temporal Q",
            question="q",
            expected_keywords=("a", "b", "c"),
            matched_keywords=("a", "b", "c"),
            missing_keywords=(),
            coverage=1.0,
            top1_coverage=0.5,
            all_covered=True,
        ),
        KeywordResult(
            id="q3",
            question_type="Temporal Q",
            question="q",
            expected_keywords=("a", "b"),
            matched_keywords=(),
            missing_keywords=("a", "b"),
            coverage=0.0,
            top1_coverage=0.0,
            all_covered=False,
            error="boom",
        ),
    ]

    overall = metrics_for(results)
    by_type = aggregate_by_type(results)

    assert overall.count == 2
    assert overall.failed == 1
    assert overall.mean_coverage == pytest.approx(0.75)
    assert overall.micro_coverage == pytest.approx(4 / 5)
    assert overall.all_covered_rate == pytest.approx(0.5)
    assert overall.keywords_expected == 5
    assert overall.keywords_matched == 4
    assert by_type["Direct Fact"].count == 1
    assert by_type["Temporal Q"].count == 1
    assert by_type["Temporal Q"].failed == 1


def test_answer_chunks_keyword_source_keeps_source_numbers():
    question = _question(
        "cash was $35.934 billion",
        "Cash and cash equivalents: $35,934 / $29,943.",
    )

    expected = build_expected_keywords(question, "answer-chunks")
    keys = {keyword.key for keyword in expected}

    assert "num:35934" in keys
    assert "num:29943" in keys
