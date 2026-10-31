"""Evaluate keyword coverage of reranked retrieval against an evalset."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Sequence

from dotenv import load_dotenv

from embeddings import AsyncEmbeddingClient, EmbeddingConfigError
from eval_retrieval import (
    DEFAULT_DOCUMENT_ID,
    DEFAULT_EVALSET,
    DEFAULT_FINAL_K,
    DEFAULT_TOP_K,
    EvalDataError,
    EvalQuestion,
    _error_text,
    _extract_tokens,
    _filter_questions,
    _normalize_text,
    _positive_int,
    _preview,
    _section,
    load_evalset,
    retrieve_reranked,
)
from qdrant_store import AsyncQdrantStore
from retrieval_service import (
    RerankerClient,
    RetrievalConfigError,
    RetrievalError,
    RetrievedChunk,
)

DEFAULT_KEYWORD_SOURCE = "grounded-reference"
KEYWORD_SOURCES = (
    "grounded-reference",
    "reference",
    "answer-chunks",
)
PREVIEW_CHARS = 420
MAX_MISSING_PREVIEW = 70

_NUMBER_TOKEN_RE = re.compile(
    r"(?<![\w.])"
    r"(?P<number>\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)"
    r"(?P<percent>\s*%|\s+percent\b)?"
    r"(?:\s*(?P<scale>billion|million|thousand|bn|mn|[bm])\b)?"
)

_EXTRA_STOPWORDS = {
    "about",
    "all",
    "approximately",
    "attributed",
    "attribute",
    "attributable",
    "came",
    "can",
    "cannot",
    "come",
    "comes",
    "compared",
    "compare",
    "could",
    "decrease",
    "decreased",
    "decreases",
    "decline",
    "declined",
    "declines",
    "describe",
    "described",
    "describes",
    "disclose",
    "disclosed",
    "discloses",
    "due",
    "explain",
    "explained",
    "explains",
    "fall",
    "falls",
    "fell",
    "filing",
    "generate",
    "generated",
    "generates",
    "grew",
    "grow",
    "growing",
    "grown",
    "grows",
    "if",
    "include",
    "included",
    "includes",
    "including",
    "increase",
    "increased",
    "increases",
    "location",
    "locations",
    "made",
    "mainly",
    "make",
    "makes",
    "may",
    "might",
    "must",
    "new",
    "partially",
    "partly",
    "percent",
    "percentage",
    "primarily",
    "reported",
    "reporting",
    "reports",
    "respectively",
    "rise",
    "rises",
    "rose",
    "said",
    "says",
    "several",
    "should",
    "state",
    "stated",
    "states",
    "such",
    "through",
    "unable",
    "warned",
    "would",
}
_KEYWORD_STOPWORDS = _EXTRA_STOPWORDS


@dataclass(frozen=True)
class Keyword:
    key: str
    label: str


@dataclass(frozen=True)
class KeywordChunk:
    rank: int
    point_id: str
    vector_score: float
    rerank_score: float
    unit_type: str
    section: str
    text: str


@dataclass(frozen=True)
class KeywordResult:
    id: str
    question_type: str
    question: str
    expected_keywords: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    missing_keywords: tuple[str, ...]
    coverage: float
    top1_coverage: float
    all_covered: bool
    chunks: tuple[KeywordChunk, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class KeywordMetrics:
    count: int
    mean_coverage: float
    micro_coverage: float
    mean_top1_coverage: float
    all_covered: int
    all_covered_rate: float
    keywords_expected: int
    keywords_matched: int
    failed: int


@dataclass(frozen=True)
class KeywordSettings:
    document_id: str
    top_k: int
    final_k: int
    keyword_source: str
    delay: float
    include_text: bool
    progress: bool


def _decimal_text(value: Decimal) -> str:
    if value == 0:
        return "0"
    text = format(value.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _is_year(value: Decimal) -> bool:
    if value != value.to_integral_value():
        return False
    return 1900 <= int(value) <= 2099


def _extract_number_keywords(text: str) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    for match in _NUMBER_TOKEN_RE.finditer(_normalize_text(text)):
        raw_number = (
            match.group("number").replace("$", "").replace(",", "")
        )
        try:
            value = Decimal(raw_number)
        except InvalidOperation:
            continue
        percent = match.group("percent") is not None
        scale = (match.group("scale") or "").lower()
        if percent:
            key = f"pct:{_decimal_text(value)}"
        else:
            if not scale and _is_year(value):
                continue
            if scale in {"billion", "bn", "b"}:
                value *= Decimal(1000)
            elif scale == "thousand":
                value /= Decimal(1000)
            key = f"num:{_decimal_text(value)}"
        label = " ".join(match.group(0).split())
        found.append((key, label))
    return found


def extract_keywords(text: str) -> tuple[Keyword, ...]:
    if not text.strip():
        return ()
    keywords: list[Keyword] = []
    seen: set[str] = set()
    for token in sorted(_extract_tokens(text) - _KEYWORD_STOPWORDS):
        if len(token) < 2:
            continue
        key = f"token:{token}"
        if key in seen:
            continue
        seen.add(key)
        keywords.append(Keyword(key=key, label=token))
    for key, label in _extract_number_keywords(text):
        if key in seen:
            continue
        seen.add(key)
        keywords.append(Keyword(key=key, label=label))
    return tuple(keywords)


def keyword_keys(text: str) -> frozenset[str]:
    return frozenset(keyword.key for keyword in extract_keywords(text))


def build_expected_keywords(
    question: EvalQuestion,
    keyword_source: str,
) -> tuple[Keyword, ...]:
    if keyword_source not in KEYWORD_SOURCES:
        raise EvalDataError(
            f"unknown keyword source {keyword_source!r}; "
            f"choose from {', '.join(KEYWORD_SOURCES)}"
        )
    source_text = " ".join(
        chunk.text for chunk in question.answer_chunks
    )
    source_keywords = extract_keywords(source_text)
    if keyword_source == "answer-chunks":
        if not source_keywords:
            raise EvalDataError(
                f"{question.id}: answer chunks produced no keywords"
            )
        return source_keywords
    reference_keywords = extract_keywords(question.reference_answer)
    if not reference_keywords:
        raise EvalDataError(
            f"{question.id}: reference answer produced no keywords"
        )
    if keyword_source == "reference":
        return reference_keywords
    source_keys = {keyword.key for keyword in source_keywords}
    grounded = tuple(
        keyword
        for keyword in reference_keywords
        if keyword.key in source_keys
    )
    if grounded:
        return grounded
    if source_keywords:
        return source_keywords
    return reference_keywords


def _chunk_record(
    rank: int,
    chunk: RetrievedChunk,
) -> KeywordChunk:
    return KeywordChunk(
        rank=rank,
        point_id=chunk.point_id,
        vector_score=chunk.vector_score,
        rerank_score=chunk.rerank_score,
        unit_type=str(chunk.payload.get("unit_type") or ""),
        section=_section(chunk.payload),
        text=chunk.text,
    )


def score_keywords(
    question: EvalQuestion,
    expected: Sequence[Keyword],
    chunks: Sequence[RetrievedChunk],
) -> KeywordResult:
    if not expected:
        raise EvalDataError(f"{question.id}: no expected keywords")
    all_keys: set[str] = set()
    for chunk in chunks:
        all_keys.update(keyword_keys(chunk.text))
    top1_keys = keyword_keys(chunks[0].text) if chunks else frozenset()
    matched = tuple(
        keyword.label for keyword in expected if keyword.key in all_keys
    )
    missing = tuple(
        keyword.label for keyword in expected if keyword.key not in all_keys
    )
    top1_matched = sum(
        1 for keyword in expected if keyword.key in top1_keys
    )
    expected_count = len(expected)
    return KeywordResult(
        id=question.id,
        question_type=question.question_type,
        question=question.question,
        expected_keywords=tuple(
            keyword.label for keyword in expected
        ),
        matched_keywords=matched,
        missing_keywords=missing,
        coverage=len(matched) / expected_count,
        top1_coverage=top1_matched / expected_count,
        all_covered=not missing,
        chunks=tuple(
            _chunk_record(rank, chunk)
            for rank, chunk in enumerate(chunks, 1)
        ),
    )


def _error_result(
    question: EvalQuestion,
    expected: Sequence[Keyword],
    error: str,
) -> KeywordResult:
    return KeywordResult(
        id=question.id,
        question_type=question.question_type,
        question=question.question,
        expected_keywords=tuple(
            keyword.label for keyword in expected
        ),
        matched_keywords=(),
        missing_keywords=tuple(
            keyword.label for keyword in expected
        ),
        coverage=0.0,
        top1_coverage=0.0,
        all_covered=False,
        error=error,
    )


async def evaluate_keywords(
    questions: Sequence[EvalQuestion],
    settings: KeywordSettings,
) -> tuple[list[KeywordResult], int]:
    if not questions:
        raise EvalDataError("no questions to evaluate")
    store = AsyncQdrantStore()
    embedder: AsyncEmbeddingClient | None = None
    reranker = RerankerClient()
    results: list[KeywordResult] = []
    try:
        embedder = AsyncEmbeddingClient()
        reranker.ensure_configured()
        document_count = await store.count(
            document_id=settings.document_id,
        )
        if document_count < 1:
            raise EvalDataError(
                "no chunks found for document_id "
                f"{settings.document_id!r} in "
                f"{store.collection_name!r}"
            )
        for number, question in enumerate(questions, 1):
            if settings.progress:
                print(
                    f"[{number:02d}/{len(questions):02d}] {question.id}",
                    file=sys.stderr,
                    flush=True,
                )
            expected: tuple[Keyword, ...] = ()
            try:
                expected = build_expected_keywords(
                    question,
                    settings.keyword_source,
                )
                chunks = await retrieve_reranked(
                    question.question,
                    embedder=embedder,
                    store=store,
                    reranker=reranker,
                    top_k=settings.top_k,
                    final_k=settings.final_k,
                    document_id=settings.document_id,
                )
                results.append(
                    score_keywords(question, expected, chunks)
                )
            except (
                EvalDataError,
                RetrievalError,
                RetrievalConfigError,
                EmbeddingConfigError,
            ) as exc:
                results.append(
                    _error_result(
                        question,
                        expected,
                        _error_text(exc),
                    )
                )
            except Exception as exc:
                results.append(
                    _error_result(
                        question,
                        expected,
                        _error_text(exc),
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


def metrics_for(results: Sequence[KeywordResult]) -> KeywordMetrics:
    successful = [result for result in results if result.error is None]
    failed = len(results) - len(successful)
    count = len(successful)
    if count == 0:
        return KeywordMetrics(
            count=0,
            mean_coverage=0.0,
            micro_coverage=0.0,
            mean_top1_coverage=0.0,
            all_covered=0,
            all_covered_rate=0.0,
            keywords_expected=0,
            keywords_matched=0,
            failed=failed,
        )
    keywords_expected = sum(
        len(result.expected_keywords) for result in successful
    )
    keywords_matched = sum(
        len(result.matched_keywords) for result in successful
    )
    all_covered = sum(1 for result in successful if result.all_covered)
    return KeywordMetrics(
        count=count,
        mean_coverage=(
            sum(result.coverage for result in successful) / count
        ),
        micro_coverage=(
            keywords_matched / keywords_expected
            if keywords_expected
            else 0.0
        ),
        mean_top1_coverage=(
            sum(result.top1_coverage for result in successful) / count
        ),
        all_covered=all_covered,
        all_covered_rate=all_covered / count,
        keywords_expected=keywords_expected,
        keywords_matched=keywords_matched,
        failed=failed,
    )


def aggregate_by_type(
    results: Sequence[KeywordResult],
) -> dict[str, KeywordMetrics]:
    grouped: dict[str, list[KeywordResult]] = {}
    for result in results:
        grouped.setdefault(result.question_type, []).append(result)
    return {
        question_type: metrics_for(group)
        for question_type, group in grouped.items()
    }


def _format_metrics(label: str, metrics: KeywordMetrics) -> str:
    return (
        f"{label:<20} "
        f"n={metrics.count:<3} "
        f"cov={metrics.mean_coverage:.3f} "
        f"micro={metrics.micro_coverage:.3f} "
        f"top1={metrics.mean_top1_coverage:.3f} "
        f"all={metrics.all_covered_rate:.3f} "
        f"kw={metrics.keywords_matched}/{metrics.keywords_expected} "
        f"failed={metrics.failed}"
    )


def _missing_preview(result: KeywordResult) -> str:
    if not result.missing_keywords:
        return "-"
    joined = ", ".join(result.missing_keywords)
    compact = _preview(joined, MAX_MISSING_PREVIEW)
    return compact


def _chunk_dict(
    chunk: KeywordChunk,
    *,
    include_text: bool,
) -> dict[str, object]:
    data = asdict(chunk)
    data["text"] = (
        chunk.text if include_text else _preview(chunk.text)
    )
    return data


def _question_dict(
    result: KeywordResult,
    *,
    include_text: bool,
) -> dict[str, object]:
    return {
        "id": result.id,
        "question_type": result.question_type,
        "question": result.question,
        "expected_keywords": list(result.expected_keywords),
        "matched_keywords": list(result.matched_keywords),
        "missing_keywords": list(result.missing_keywords),
        "coverage": result.coverage,
        "top1_coverage": result.top1_coverage,
        "all_covered": result.all_covered,
        "error": result.error,
        "chunks": [
            _chunk_dict(chunk, include_text=include_text)
            for chunk in result.chunks
        ],
    }


def build_report(
    results: Sequence[KeywordResult],
    *,
    settings: KeywordSettings,
    document_count: int,
    collection_name: str,
) -> dict[str, object]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_id": settings.document_id,
        "collection": collection_name,
        "document_chunk_count": document_count,
        "top_k": settings.top_k,
        "final_k": settings.final_k,
        "keyword_source": settings.keyword_source,
        "overall": asdict(metrics_for(results)),
        "by_question_type": {
            question_type: asdict(metrics)
            for question_type, metrics in aggregate_by_type(
                results
            ).items()
        },
        "questions": [
            _question_dict(result, include_text=settings.include_text)
            for result in results
        ],
    }


def print_report(
    results: Sequence[KeywordResult],
    *,
    settings: KeywordSettings,
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
        f"keyword_source={settings.keyword_source}"
    )
    print(
        "Coverage: expected keywords found in the union of "
        "reranked chunks; top1 uses only rank 1"
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
        f"{'ID':<6} {'Type':<16} {'Cov':>6} {'Top1':>6} "
        f"{'Matched':>9} {'Top point':<12} {'Missing':<50}"
    )
    for result in results:
        if result.error:
            print(
                f"{result.id:<6} {result.question_type:<16} "
                f"ERROR: {result.error}"
            )
            continue
        top_point = result.chunks[0].point_id if result.chunks else "-"
        print(
            f"{result.id:<6} {result.question_type:<16} "
            f"{result.coverage:>6.3f} "
            f"{result.top1_coverage:>6.3f} "
            f"{len(result.matched_keywords):>4}/"
            f"{len(result.expected_keywords):<4} "
            f"{top_point[:12]:<12} "
            f"{_missing_preview(result):<50}"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Compute keyword coverage of reranked chunks for an "
            "AAPL 2025 10-K evalset."
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
        help=f"reranked chunks per question (default: {DEFAULT_FINAL_K})",
    )
    parser.add_argument(
        "--keyword-source",
        choices=KEYWORD_SOURCES,
        default=DEFAULT_KEYWORD_SOURCE,
        help=(
            "expected-keyword source: grounded-reference keeps only "
            "reference keywords present in answer chunks; reference "
            "uses the full reference answer; answer-chunks uses all "
            "answer-chunk keywords "
            f"(default: {DEFAULT_KEYWORD_SOURCE})"
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
        print(
            "error: --document-id cannot be empty",
            file=sys.stderr,
        )
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
        settings = KeywordSettings(
            document_id=args.document_id.strip(),
            top_k=args.top_k,
            final_k=args.final_k,
            keyword_source=args.keyword_source,
            delay=args.delay,
            include_text=args.include_text,
            progress=not args.quiet,
        )
        results, document_count = asyncio.run(
            evaluate_keywords(questions, settings)
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
