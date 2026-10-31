"""Evaluate reranked retrieval with MRR against the AAPL 2025 10-K evalset."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from dotenv import load_dotenv

from embeddings import AsyncEmbeddingClient, EmbeddingConfigError
from qdrant_store import AsyncQdrantStore
from retrieval_service import (
    DEFAULT_FINAL_K,
    DEFAULT_TOP_K,
    RerankerClient,
    RetrievalConfigError,
    RetrievalError,
    RetrievedChunk,
)

DEFAULT_EVALSET = Path(__file__).with_name("evalset.jsonl")
DEFAULT_DOCUMENT_ID = "aapl_2025_10k"
DEFAULT_THRESHOLD = 0.35
DEFAULT_NUMERIC_WEIGHT = 0.75
PREVIEW_CHARS = 420

_NUMBER_RE = re.compile(
    r"(?<![\w.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)
_TOKEN_RE = re.compile(r"[a-z][a-z0-9'-]*")
_SEGMENT_RE = re.compile(r"(?<=[.!?])\s+|\s*;\s*|\n+|\s*[\u2014\u2013\ufffd]\s*")
_BULLET_RE = re.compile(r"^[\-\*\u2022]+\s*")
_SPACE_RE = re.compile(r"\s+")

_STOPWORDS = {
    "a",
    "about",
    "also",
    "an",
    "and",
    "apple",
    "approximately",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "billion",
    "billions",
    "by",
    "company",
    "did",
    "do",
    "does",
    "ended",
    "fiscal",
    "for",
    "from",
    "had",
    "has",
    "have",
    "how",
    "in",
    "inc",
    "into",
    "is",
    "it",
    "its",
    "million",
    "millions",
    "more",
    "most",
    "of",
    "on",
    "or",
    "over",
    "respectively",
    "september",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "this",
    "those",
    "to",
    "under",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "with",
    "within",
    "year",
    "years",
}


class EvalDataError(ValueError):
    """Raised when the evaluation set is malformed."""


@dataclass(frozen=True)
class AnswerChunk:
    chunk_id: str
    text: str


@dataclass(frozen=True)
class EvalQuestion:
    id: str
    question_type: str
    question: str
    reference_answer: str
    answer_chunks: tuple[AnswerChunk, ...]
    source_document: str
    source_pages: tuple[int, ...]


@dataclass(frozen=True)
class EvidenceSpan:
    chunk_id: str
    label: str
    text: str
    numbers: frozenset[str]
    tokens: frozenset[str]


@dataclass(frozen=True)
class EvidenceMatch:
    score: float
    relevant: bool
    chunk_id: str
    span_label: str
    span_text: str
    matched_numbers: int
    span_numbers: int
    matched_tokens: int
    span_tokens: int
    numeric_recall: float
    token_recall: float


@dataclass(frozen=True)
class ChunkAssessment:
    rank: int
    point_id: str
    vector_score: float
    rerank_score: float
    relevant: bool
    evidence: EvidenceMatch | None
    unit_type: str
    section: str
    text: str


@dataclass(frozen=True)
class QuestionResult:
    id: str
    question_type: str
    question: str
    reference_answer: str
    source_document: str = ""
    source_pages: tuple[int, ...] = ()
    answer_chunk_ids: tuple[str, ...] = ()
    rank: int | None = None
    reciprocal_rank: float = 0.0
    best_score: float = 0.0
    assessments: tuple[ChunkAssessment, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class Metrics:
    count: int
    mrr: float
    hit_at_1: float
    hit_at_3: float
    hit_at_5: float
    no_match: int
    failed: int
    mean_best_score: float


@dataclass(frozen=True)
class EvalSettings:
    document_id: str
    top_k: int
    final_k: int
    threshold: float
    numeric_weight: float
    delay: float
    include_text: bool
    progress: bool


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    normalized = normalized.replace("\u2019", "'").replace("\u2018", "'")
    return _SPACE_RE.sub(" ", normalized).lower().strip()


def _stem(token: str) -> str:
    if token.endswith("'s"):
        token = token[:-2]
    if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
        token = token[:-1]
    return token


def _extract_numbers(text: str) -> frozenset[str]:
    numbers: set[str] = set()
    for match in _NUMBER_RE.finditer(_normalize_text(text)):
        raw = match.group(0).replace(",", "").rstrip("%")
        if not raw:
            continue
        if (
            raw.isdigit()
            and len(raw) == 4
            and "." not in raw
            and 1900 <= int(raw) <= 2099
        ):
            continue
        numbers.add(raw)
    return frozenset(numbers)


def _extract_tokens(text: str) -> frozenset[str]:
    tokens: set[str] = set()
    for raw in _TOKEN_RE.findall(_normalize_text(text)):
        token = _stem(raw.strip("'-"))
        if not token or token in _STOPWORDS:
            continue
        tokens.add(token)
    return frozenset(tokens)


def _split_evidence(text: str) -> list[str]:
    parts: list[str] = []
    for part in _SEGMENT_RE.split(text):
        cleaned = _BULLET_RE.sub("", part.strip())
        if not cleaned:
            continue
        if not _extract_numbers(cleaned) and len(_extract_tokens(cleaned)) < 2:
            continue
        parts.append(cleaned)
    return parts


def build_evidence_spans(
    answer_chunks: Sequence[AnswerChunk],
) -> tuple[EvidenceSpan, ...]:
    spans: list[EvidenceSpan] = []
    seen: set[str] = set()
    for answer_chunk in answer_chunks:
        candidates = [("full", answer_chunk.text)]
        candidates.extend(
            (f"part-{index}", part)
            for index, part in enumerate(
                _split_evidence(answer_chunk.text),
                1,
            )
        )
        for label, text in candidates:
            cleaned = _SPACE_RE.sub(" ", text).strip()
            if not cleaned:
                continue
            marker = _normalize_text(cleaned)
            if marker in seen:
                continue
            seen.add(marker)
            numbers = _extract_numbers(cleaned)
            tokens = _extract_tokens(cleaned)
            if not numbers and len(tokens) < 2:
                continue
            spans.append(
                EvidenceSpan(
                    chunk_id=answer_chunk.chunk_id,
                    label=label,
                    text=cleaned,
                    numbers=numbers,
                    tokens=tokens,
                )
            )
    if not spans:
        raise EvalDataError("answer chunks produced no usable evidence spans")
    return tuple(spans)


def match_candidate(
    candidate_text: str,
    spans: Sequence[EvidenceSpan],
    *,
    numeric_weight: float = DEFAULT_NUMERIC_WEIGHT,
    threshold: float = DEFAULT_THRESHOLD,
) -> EvidenceMatch | None:
    if not 0.0 <= numeric_weight <= 1.0:
        raise ValueError("numeric_weight must be between 0 and 1")
    candidate_numbers = _extract_numbers(candidate_text)
    candidate_tokens = _extract_tokens(candidate_text)
    if not candidate_numbers and not candidate_tokens:
        return None

    best: EvidenceMatch | None = None
    best_key: tuple[float, float, float, int] | None = None
    for span in spans:
        matched_numbers = len(span.numbers & candidate_numbers)
        matched_tokens = len(span.tokens & candidate_tokens)
        span_number_count = len(span.numbers)
        span_token_count = len(span.tokens)
        numeric_recall = (
            matched_numbers / span_number_count if span_number_count else 0.0
        )
        token_recall = (
            matched_tokens / span_token_count if span_token_count else 0.0
        )
        if span_number_count:
            score = (
                numeric_weight * numeric_recall
                + (1.0 - numeric_weight) * token_recall
            )
        else:
            score = token_recall
        key = (score, numeric_recall, token_recall, matched_numbers)
        if best_key is not None and key <= best_key:
            continue
        best_key = key
        best = EvidenceMatch(
            score=score,
            relevant=score >= threshold,
            chunk_id=span.chunk_id,
            span_label=span.label,
            span_text=span.text,
            matched_numbers=matched_numbers,
            span_numbers=span_number_count,
            matched_tokens=matched_tokens,
            span_tokens=span_token_count,
            numeric_recall=numeric_recall,
            token_recall=token_recall,
        )
    return best


def _chunk_text(payload: dict[str, Any]) -> tuple[str, str]:
    text = str(payload.get("text") or "").strip()
    embed_text = str(payload.get("embedding_text") or text).strip()
    if not text:
        text = embed_text
    return text, embed_text


def _section(payload: dict[str, Any]) -> str:
    hierarchy = [
        str(value).strip()
        for value in payload.get("hierarchy") or []
        if str(value).strip()
    ]
    if hierarchy:
        return " > ".join(hierarchy)
    return str(payload.get("item_title") or payload.get("part") or "").strip()


async def retrieve_reranked(
    question: str,
    *,
    embedder: AsyncEmbeddingClient,
    store: AsyncQdrantStore,
    reranker: RerankerClient,
    top_k: int,
    final_k: int,
    document_id: str,
) -> list[RetrievedChunk]:
    vector = await embedder.embed_query(question)
    hits = await store.search(
        vector,
        limit=top_k,
        document_id=document_id,
    )
    records: list[tuple[Any, str, str]] = []
    for hit in hits:
        text, embed_text = _chunk_text(hit.payload)
        if not text or not embed_text:
            continue
        records.append((hit, text, embed_text))
    if not records:
        return []
    ranked = await reranker.rerank(
        question,
        [embed_text for _, _, embed_text in records],
        top_n=final_k,
    )
    chunks: list[RetrievedChunk] = []
    for candidate_index, score in ranked:
        if candidate_index < 0 or candidate_index >= len(records):
            continue
        hit, text, _ = records[candidate_index]
        chunks.append(
            RetrievedChunk(
                point_id=hit.point_id,
                text=text,
                vector_score=hit.score,
                rerank_score=score,
                payload=dict(hit.payload),
            )
        )
    return chunks


def assess_reranked(
    chunks: Sequence[RetrievedChunk],
    spans: Sequence[EvidenceSpan],
    *,
    numeric_weight: float = DEFAULT_NUMERIC_WEIGHT,
    threshold: float = DEFAULT_THRESHOLD,
) -> tuple[ChunkAssessment, ...]:
    assessments: list[ChunkAssessment] = []
    for rank, chunk in enumerate(chunks, 1):
        evidence = match_candidate(
            chunk.text,
            spans,
            numeric_weight=numeric_weight,
            threshold=threshold,
        )
        assessments.append(
            ChunkAssessment(
                rank=rank,
                point_id=chunk.point_id,
                vector_score=chunk.vector_score,
                rerank_score=chunk.rerank_score,
                relevant=bool(evidence and evidence.relevant),
                evidence=evidence,
                unit_type=str(chunk.payload.get("unit_type") or ""),
                section=_section(chunk.payload),
                text=chunk.text,
            )
        )
    return tuple(assessments)


def first_relevant_rank(
    assessments: Sequence[ChunkAssessment],
) -> int | None:
    for assessment in assessments:
        if assessment.relevant:
            return assessment.rank
    return None


def metrics_for(results: Sequence[QuestionResult]) -> Metrics:
    successful = [result for result in results if result.error is None]
    failed = len(results) - len(successful)
    count = len(successful)
    if count == 0:
        return Metrics(
            count=0,
            mrr=0.0,
            hit_at_1=0.0,
            hit_at_3=0.0,
            hit_at_5=0.0,
            no_match=0,
            failed=failed,
            mean_best_score=0.0,
        )
    mrr = sum(result.reciprocal_rank for result in successful) / count
    hit_at_1 = (
        sum(
            1
            for result in successful
            if result.rank is not None and result.rank <= 1
        )
        / count
    )
    hit_at_3 = (
        sum(
            1
            for result in successful
            if result.rank is not None and result.rank <= 3
        )
        / count
    )
    hit_at_5 = (
        sum(
            1
            for result in successful
            if result.rank is not None and result.rank <= 5
        )
        / count
    )
    no_match = sum(1 for result in successful if result.rank is None)
    mean_best_score = (
        sum(result.best_score for result in successful) / count
    )
    return Metrics(
        count=count,
        mrr=mrr,
        hit_at_1=hit_at_1,
        hit_at_3=hit_at_3,
        hit_at_5=hit_at_5,
        no_match=no_match,
        failed=failed,
        mean_best_score=mean_best_score,
    )


def aggregate_by_type(
    results: Sequence[QuestionResult],
) -> dict[str, Metrics]:
    grouped: dict[str, list[QuestionResult]] = {}
    for result in results:
        grouped.setdefault(result.question_type, []).append(result)
    return {
        question_type: metrics_for(group)
        for question_type, group in grouped.items()
    }


def _load_question(raw: Any, line_number: int) -> EvalQuestion:
    if not isinstance(raw, dict):
        raise EvalDataError(f"line {line_number}: expected a JSON object")
    required = ("id", "question_type", "question", "answer_chunks")
    for field in required:
        if not raw.get(field):
            raise EvalDataError(
                f"line {line_number}: missing required field {field!r}"
            )
    raw_chunks = raw.get("answer_chunks")
    if not isinstance(raw_chunks, list):
        raise EvalDataError(
            f"line {line_number}: answer_chunks must be a list"
        )
    answer_chunks: list[AnswerChunk] = []
    for index, raw_chunk in enumerate(raw_chunks, 1):
        if not isinstance(raw_chunk, dict):
            raise EvalDataError(
                f"line {line_number}: answer_chunks[{index}] must be an object"
            )
        text = str(raw_chunk.get("text") or "").strip()
        if not text:
            raise EvalDataError(
                f"line {line_number}: answer_chunks[{index}] has no text"
            )
        chunk_id = str(
            raw_chunk.get("chunk_id") or f"{raw['id']}_chunk_{index}"
        ).strip()
        answer_chunks.append(AnswerChunk(chunk_id=chunk_id, text=text))
    pages: list[int] = []
    for page in raw.get("source_pages") or []:
        try:
            pages.append(int(page))
        except (TypeError, ValueError):
            continue
    return EvalQuestion(
        id=str(raw["id"]).strip(),
        question_type=str(raw["question_type"]).strip(),
        question=str(raw["question"]).strip(),
        reference_answer=str(raw.get("reference_answer") or "").strip(),
        answer_chunks=tuple(answer_chunks),
        source_document=str(raw.get("source_document") or "").strip(),
        source_pages=tuple(pages),
    )


def load_evalset(path: Path) -> list[EvalQuestion]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise EvalDataError(f"cannot read evalset {path}: {exc}") from exc
    questions: list[EvalQuestion] = []
    seen_ids: set[str] = set()
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise EvalDataError(
                f"line {line_number}: invalid JSON: {exc}"
            ) from exc
        question = _load_question(raw, line_number)
        if question.id in seen_ids:
            raise EvalDataError(
                f"line {line_number}: duplicate question id {question.id!r}"
            )
        seen_ids.add(question.id)
        questions.append(question)
    if not questions:
        raise EvalDataError(f"evalset {path} contains no questions")
    return questions


def _filter_questions(
    questions: Sequence[EvalQuestion],
    question_types: Sequence[str],
    limit: int | None,
) -> list[EvalQuestion]:
    allowed = {
        value.strip()
        for value in question_types
        if value.strip()
    }
    if allowed:
        questions = [
            question
            for question in questions
            if question.question_type in allowed
        ]
        if not questions:
            raise EvalDataError(
                "no questions match --question-type "
                + ", ".join(sorted(allowed))
            )
    if limit is not None:
        if limit < 1:
            raise EvalDataError("--limit must be at least 1")
        questions = questions[:limit]
    return list(questions)


def _error_text(exc: Exception) -> str:
    message = str(exc).strip()
    return f"{type(exc).__name__}: {message}" if message else type(exc).__name__


async def evaluate(
    questions: Sequence[EvalQuestion],
    settings: EvalSettings,
) -> tuple[list[QuestionResult], int]:
    if not questions:
        raise EvalDataError("no questions to evaluate")
    store = AsyncQdrantStore()
    embedder: AsyncEmbeddingClient | None = None
    reranker = RerankerClient()
    results: list[QuestionResult] = []
    try:
        embedder = AsyncEmbeddingClient()
        reranker.ensure_configured()
        document_count = await store.count(
            document_id=settings.document_id,
        )
        if document_count < 1:
            raise EvalDataError(
                "no chunks found for document_id "
                f"{settings.document_id!r} in {store.collection_name!r}"
            )
        for number, question in enumerate(questions, 1):
            if settings.progress:
                print(
                    f"[{number:02d}/{len(questions):02d}] {question.id}",
                    file=sys.stderr,
                    flush=True,
                )
            try:
                spans = build_evidence_spans(question.answer_chunks)
                chunks = await retrieve_reranked(
                    question.question,
                    embedder=embedder,
                    store=store,
                    reranker=reranker,
                    top_k=settings.top_k,
                    final_k=settings.final_k,
                    document_id=settings.document_id,
                )
                assessments = assess_reranked(
                    chunks,
                    spans,
                    numeric_weight=settings.numeric_weight,
                    threshold=settings.threshold,
                )
                rank = first_relevant_rank(assessments)
                results.append(
                    QuestionResult(
                        id=question.id,
                        question_type=question.question_type,
                        question=question.question,
                        reference_answer=question.reference_answer,
                        source_document=question.source_document,
                        source_pages=question.source_pages,
                        answer_chunk_ids=tuple(
                            chunk.chunk_id
                            for chunk in question.answer_chunks
                        ),
                        rank=rank,
                        reciprocal_rank=1.0 / rank if rank else 0.0,
                        best_score=max(
                            (
                                assessment.evidence.score
                                for assessment in assessments
                                if assessment.evidence is not None
                            ),
                            default=0.0,
                        ),
                        assessments=assessments,
                    )
                )
            except (
                EvalDataError,
                RetrievalError,
                RetrievalConfigError,
                EmbeddingConfigError,
            ) as exc:
                results.append(
                    QuestionResult(
                        id=question.id,
                        question_type=question.question_type,
                        question=question.question,
                        reference_answer=question.reference_answer,
                        source_document=question.source_document,
                        source_pages=question.source_pages,
                        answer_chunk_ids=tuple(
                            chunk.chunk_id
                            for chunk in question.answer_chunks
                        ),
                        error=_error_text(exc),
                    )
                )
            except Exception as exc:
                results.append(
                    QuestionResult(
                        id=question.id,
                        question_type=question.question_type,
                        question=question.question,
                        reference_answer=question.reference_answer,
                        source_document=question.source_document,
                        source_pages=question.source_pages,
                        answer_chunk_ids=tuple(
                            chunk.chunk_id
                            for chunk in question.answer_chunks
                        ),
                        error=_error_text(exc),
                    )
                )
            if settings.delay > 0 and number < len(questions):
                await asyncio.sleep(settings.delay)
        return results, document_count
    finally:
        await reranker.close()
        if embedder is not None:
            await embedder.close()
        await store.close()


def _format_metrics(label: str, metrics: Metrics) -> str:
    return (
        f"{label:<20} "
        f"n={metrics.count:<3} "
        f"MRR={metrics.mrr:.4f} "
        f"Hit@1={metrics.hit_at_1:.3f} "
        f"Hit@3={metrics.hit_at_3:.3f} "
        f"Hit@5={metrics.hit_at_5:.3f} "
        f"best={metrics.mean_best_score:.3f} "
        f"no_match={metrics.no_match} "
        f"failed={metrics.failed}"
    )


def _rank_label(result: QuestionResult) -> str:
    return str(result.rank) if result.rank else "-"


def _matched_label(result: QuestionResult) -> str:
    for assessment in result.assessments:
        if assessment.relevant and assessment.evidence is not None:
            return assessment.evidence.chunk_id
    return "-"


def _preview(text: str, limit: int = PREVIEW_CHARS) -> str:
    compact = _SPACE_RE.sub(" ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "..."


def _assessment_dict(
    assessment: ChunkAssessment,
    *,
    include_text: bool,
) -> dict[str, Any]:
    data = asdict(assessment)
    data["text"] = assessment.text if include_text else _preview(
        assessment.text
    )
    if assessment.evidence is not None:
        data["evidence"]["span_text"] = (
            assessment.evidence.span_text
            if include_text
            else _preview(assessment.evidence.span_text)
        )
    return data


def _question_dict(
    result: QuestionResult,
    *,
    include_text: bool,
) -> dict[str, Any]:
    return {
        "id": result.id,
        "question_type": result.question_type,
        "question": result.question,
        "reference_answer": result.reference_answer,
        "source_document": result.source_document,
        "source_pages": list(result.source_pages),
        "answer_chunk_ids": list(result.answer_chunk_ids),
        "rank": result.rank,
        "reciprocal_rank": result.reciprocal_rank,
        "best_score": result.best_score,
        "error": result.error,
        "assessments": [
            _assessment_dict(assessment, include_text=include_text)
            for assessment in result.assessments
        ],
    }


def build_report(
    results: Sequence[QuestionResult],
    *,
    settings: EvalSettings,
    document_count: int,
    collection_name: str,
) -> dict[str, Any]:
    overall = metrics_for(results)
    by_type = aggregate_by_type(results)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_id": settings.document_id,
        "collection": collection_name,
        "document_chunk_count": document_count,
        "top_k": settings.top_k,
        "final_k": settings.final_k,
        "threshold": settings.threshold,
        "numeric_weight": settings.numeric_weight,
        "overall": asdict(overall),
        "by_question_type": {
            question_type: asdict(metrics)
            for question_type, metrics in by_type.items()
        },
        "questions": [
            _question_dict(result, include_text=settings.include_text)
            for result in results
        ],
    }


def print_report(
    results: Sequence[QuestionResult],
    *,
    settings: EvalSettings,
    document_count: int,
    collection_name: str,
    quiet: bool = False,
) -> None:
    overall = metrics_for(results)
    by_type = aggregate_by_type(results)
    print(
        f"Collection: {collection_name} | "
        f"document_id: {settings.document_id} | "
        f"chunks: {document_count}"
    )
    print(
        f"Questions: {len(results)} | top_k={settings.top_k} | "
        f"final_k={settings.final_k} | "
        f"threshold={settings.threshold:.2f} | "
        f"numeric_weight={settings.numeric_weight:.2f}"
    )
    print(
        "Relevance: coverage of answer-chunk evidence spans, "
        "measured on reranked chunks"
    )
    print()
    print(_format_metrics("Overall", overall))
    print()
    print("By question type:")
    for question_type, metrics in by_type.items():
        print(_format_metrics(question_type, metrics))
    if quiet:
        return
    print()
    print("Per question:")
    print(
        f"{'ID':<6} {'Type':<16} {'Rank':>4} {'RR':>6} "
        f"{'Best':>6} {'Matched':<20} {'Top point':<12} Section"
    )
    for result in results:
        if result.error:
            print(f"{result.id:<6} {result.question_type:<16} ERROR: {result.error}")
            continue
        top_point = (
            result.assessments[0].point_id if result.assessments else "-"
        )
        section = (
            result.assessments[0].section if result.assessments else ""
        )
        print(
            f"{result.id:<6} {result.question_type:<16} "
            f"{_rank_label(result):>4} "
            f"{result.reciprocal_rank:>6.3f} "
            f"{result.best_score:>6.3f} "
            f"{_matched_label(result):<20} "
            f"{top_point[:12]:<12} "
            f"{section[:60]}"
        )


def _positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def _unit_interval(value: str) -> float:
    parsed = float(value)
    if not 0.0 <= parsed <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compute MRR on reranked chunks for the AAPL 2025 10-K evalset."
        )
    )
    parser.add_argument(
        "--evalset",
        type=Path,
        default=DEFAULT_EVALSET,
        help=f"JSONL evalset (default: {DEFAULT_EVALSET})",
    )
    parser.add_argument(
        "--document-id",
        default=DEFAULT_DOCUMENT_ID,
        help=f"Qdrant document_id filter (default: {DEFAULT_DOCUMENT_ID})",
    )
    parser.add_argument(
        "--top-k",
        type=_positive_int,
        default=DEFAULT_TOP_K,
        help=f"vector candidates before reranking (default: {DEFAULT_TOP_K})",
    )
    parser.add_argument(
        "--final-k",
        type=_positive_int,
        default=DEFAULT_FINAL_K,
        help=f"reranked chunks evaluated per question (default: {DEFAULT_FINAL_K})",
    )
    parser.add_argument(
        "--threshold",
        type=_unit_interval,
        default=DEFAULT_THRESHOLD,
        help=f"relevance threshold (default: {DEFAULT_THRESHOLD})",
    )
    parser.add_argument(
        "--numeric-weight",
        type=_unit_interval,
        default=DEFAULT_NUMERIC_WEIGHT,
        help=(
            "weight for numeric coverage when matching an answer span "
            f"(default: {DEFAULT_NUMERIC_WEIGHT})"
        ),
    )
    parser.add_argument(
        "--question-type",
        action="append",
        default=[],
        help="restrict to a question_type; repeat for multiple types",
    )
    parser.add_argument(
        "--limit",
        type=_positive_int,
        default=None,
        help="evaluate only the first N selected questions",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.0,
        help="seconds to wait between questions (default: 0)",
    )
    parser.add_argument(
        "--json-out",
        type=Path,
        default=None,
        help="write full per-question results as JSON",
    )
    parser.add_argument(
        "--include-text",
        action="store_true",
        help="include full chunk text in JSON output",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only aggregate metrics",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not args.document_id.strip():
        print("error: --document-id cannot be empty", file=sys.stderr)
        return 2
    if args.delay < 0:
        print("error: --delay cannot be negative", file=sys.stderr)
        return 2
    load_dotenv(Path(__file__).with_name(".env"))
    try:
        questions = _filter_questions(
            load_evalset(args.evalset),
            args.question_type,
            args.limit,
        )
        settings = EvalSettings(
            document_id=args.document_id.strip(),
            top_k=args.top_k,
            final_k=args.final_k,
            threshold=args.threshold,
            numeric_weight=args.numeric_weight,
            delay=args.delay,
            include_text=args.include_text,
            progress=not args.quiet,
        )
        results, document_count = asyncio.run(
            evaluate(questions, settings)
        )
    except (
        EvalDataError,
        RetrievalConfigError,
        EmbeddingConfigError,
        OSError,
    ) as exc:
        print(f"error: {_error_text(exc)}", file=sys.stderr)
        return 2
    collection_name = (
        os.getenv("QDRANT_COLLECTION") or "sec_sentences"
    )
    print_report(
        results,
        settings=settings,
        document_count=document_count,
        collection_name=collection_name,
        quiet=args.quiet,
    )
    if args.json_out is not None:
        report = build_report(
            results,
            settings=settings,
            document_count=document_count,
            collection_name=collection_name,
        )
        try:
            args.json_out.write_text(
                json.dumps(report, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except OSError as exc:
            print(
                f"error: cannot write {args.json_out}: {exc}",
                file=sys.stderr,
            )
            return 2
        print(f"Wrote {args.json_out}")
    return 1 if any(result.error for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
