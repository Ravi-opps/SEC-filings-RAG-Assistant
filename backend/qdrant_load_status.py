"""Report whether a filing has finished loading into Qdrant.

Run from the repository root with the backend virtualenv::

    backend\\.venv\\Scripts\\python.exe backend\\qdrant_load_status.py NFLX --year 2025 --form 10-K

The report combines three sources:

* Redis document metadata: the expected number of chunks.
* Redis pending-chunk set: chunks still waiting to be embedded/upserted.
* Qdrant point count: chunks actually searchable.

Watch a document until it is complete (Ctrl+C stops early)::

    backend\\.venv\\Scripts\\python.exe backend\\qdrant_load_status.py NFLX --year 2025 --form 10-K --watch --interval 5 --log-file qdrant_load_status.log

Pass ``--create-index`` once to add a keyword payload index for
``document_id``; this makes Qdrant counts fast instead of a full scan.

Exit codes: 0 complete, 1 incomplete/unknown, 2 not found or connection error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv
from qdrant_client import models

from chunk_store import (
    ChunkStore,
    document_chunks_key,
    document_status_key,
    indexed_document_key,
)
from qdrant_store import QdrantStore

_META_SCAN = "sec:doc:v1:*:meta"
_INDEXED_SCAN = "sec:indexed:v1:*"
_SCAN_PAGE_SIZE = 512


@dataclass(frozen=True)
class DocumentHint:
    document_id: str
    meta: dict[str, Any]


@dataclass(frozen=True)
class LoadReport:
    document_id: str
    ticker: str
    company: str
    form_type: str
    report_date: str
    accession_number: str
    expected_chunks: int | None
    loaded_chunks: int
    pending_chunks: int
    percent_loaded: float | None
    remaining_chunks: int | None
    percent_remaining: float | None
    complete: bool | None
    state: str
    verdict: str
    count_source: str
    redis_status: str
    indexed_at: str
    notes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "ticker": self.ticker,
            "company": self.company,
            "form_type": self.form_type,
            "report_date": self.report_date,
            "accession_number": self.accession_number,
            "expected_chunks": self.expected_chunks,
            "loaded_chunks": self.loaded_chunks,
            "remaining_chunks": self.remaining_chunks,
            "pending_chunks": self.pending_chunks,
            "percent_loaded": self.percent_loaded,
            "percent_remaining": self.percent_remaining,
            "complete": self.complete,
            "state": self.state,
            "verdict": self.verdict,
            "count_source": self.count_source,
            "redis_status": self.redis_status,
            "indexed_at": self.indexed_at,
            "notes": list(self.notes),
        }


def _json_dict(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _first_int(*values: Any) -> int | None:
    for value in values:
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number >= 0:
            return number
    return None


def _text(meta: dict[str, Any], indexed: dict[str, Any], key: str) -> str:
    return str(meta.get(key) or indexed.get(key) or "")


def _ticker_from_document_id(document_id: str) -> str:
    head = document_id.split("_", 1)[0]
    return head.upper() if head else ""


def _normalise_form(value: Any) -> str:
    return str(value or "").strip().upper()


def _matches(
    meta: dict[str, Any],
    *,
    ticker: str | None,
    year: int | None,
    form: str | None,
) -> bool:
    if ticker and str(meta.get("ticker") or "").upper() != ticker.upper():
        return False
    if form and _normalise_form(meta.get("form_type")) != _normalise_form(form):
        return False
    if year is not None:
        report_date = str(meta.get("report_date") or "")
        document_id = str(meta.get("document_id") or "")
        if report_date[:4] != str(year) and f"_{year}_" not in document_id:
            return False
    return True


def _safe_meta(chunk_store: ChunkStore, document_id: str) -> dict[str, Any]:
    try:
        meta = chunk_store.get_meta(document_id)
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def _load_status(
    chunk_store: ChunkStore,
    document_id: str,
) -> dict[str, Any]:
    return _json_dict(
        chunk_store.client.get(document_status_key(document_id))
    )


def _load_indexed(
    chunk_store: ChunkStore,
    document_id: str,
    meta: dict[str, Any],
) -> dict[str, Any]:
    accession = str(meta.get("accession_number") or "")
    keys: list[str] = []
    if accession:
        keys.append(indexed_document_key(document_id, accession))
    keys.append(indexed_document_key(document_id))
    seen: set[str] = set()
    for key in keys:
        if key in seen:
            continue
        seen.add(key)
        value = chunk_store.get_indexed(key)
        if value:
            return value
    for key in chunk_store.client.scan_iter(match=_INDEXED_SCAN, count=200):
        value = _json_dict(chunk_store.client.get(key))
        if str(value.get("document_id") or "") == document_id:
            return value
    return {}


def _pending_chunks(chunk_store: ChunkStore, document_id: str) -> int:
    return int(chunk_store.client.zcard(document_chunks_key(document_id)))


def load_documents(
    chunk_store: ChunkStore,
    *,
    ticker: str | None,
    year: int | None,
    form: str | None,
    document_id: str | None,
) -> list[DocumentHint]:
    """Find the requested document from Redis metadata or indexed records."""
    if document_id:
        return [
            DocumentHint(
                document_id=document_id,
                meta=_safe_meta(chunk_store, document_id),
            )
        ]

    found: dict[str, dict[str, Any]] = {}
    for key in chunk_store.client.scan_iter(match=_META_SCAN, count=200):
        meta = _json_dict(chunk_store.client.get(key))
        doc_id = str(meta.get("document_id") or "")
        if doc_id and _matches(meta, ticker=ticker, year=year, form=form):
            found[doc_id] = meta

    for key in chunk_store.client.scan_iter(match=_INDEXED_SCAN, count=200):
        payload = _json_dict(chunk_store.client.get(key))
        doc_id = str(payload.get("document_id") or "")
        if not doc_id or doc_id in found:
            continue
        meta = {
            "document_id": doc_id,
            "ticker": payload.get("ticker"),
            "company": payload.get("company"),
            "form_type": payload.get("form_type"),
            "accession_number": payload.get("accession_number"),
            "report_date": payload.get("report_date"),
            "chunk_count": payload.get("points_upserted")
            or payload.get("records"),
        }
        if _matches(meta, ticker=ticker, year=year, form=form):
            found[doc_id] = meta

    hints = [
        DocumentHint(document_id=doc_id, meta=meta)
        for doc_id, meta in found.items()
    ]
    hints.sort(
        key=lambda hint: (
            str(hint.meta.get("report_date") or ""),
            hint.document_id,
        ),
        reverse=True,
    )
    return hints


def _is_missing_index_error(exc: BaseException) -> bool:
    message = str(exc).lower()
    return (
        "index required but not found" in message
        or "create an index" in message
    )


def _scan_count(
    store: QdrantStore,
    document_id: str,
) -> int:
    total = 0
    offset: Any = None
    while True:
        points, offset = store.client.scroll(
            collection_name=store.collection_name,
            limit=_SCAN_PAGE_SIZE,
            offset=offset,
            with_payload=["document_id"],
            with_vectors=False,
        )
        for point in points:
            payload = point.payload or {}
            if str(payload.get("document_id") or "") == document_id:
                total += 1
        if offset is None:
            return total


def count_qdrant(
    store: QdrantStore,
    document_id: str,
) -> tuple[int, str]:
    if not store.collection_exists():
        return 0, "collection missing"
    try:
        return int(store.count(document_id=document_id)), "payload filter"
    except Exception as exc:
        if not _is_missing_index_error(exc):
            raise
        return (
            _scan_count(store, document_id),
            "full collection scan (document_id index missing)",
        )


def ensure_document_index(store: QdrantStore) -> None:
    """Create the keyword index needed by filtered count queries."""
    if not store.collection_exists():
        raise RuntimeError(
            f"Qdrant collection {store.collection_name!r} does not exist"
        )
    try:
        store.client.create_payload_index(
            collection_name=store.collection_name,
            field_name="document_id",
            field_schema=models.PayloadSchemaType.KEYWORD,
            wait=True,
        )
    except Exception as exc:
        if "already exists" not in str(exc).lower():
            raise


def build_report(
    *,
    document_id: str,
    meta: dict[str, Any],
    redis_status: dict[str, Any],
    indexed: dict[str, Any],
    pending_chunks: int,
    loaded_chunks: int,
    count_source: str,
) -> LoadReport:
    expected_chunks = _first_int(
        meta.get("chunk_count"),
        indexed.get("points_upserted"),
        indexed.get("records"),
        redis_status.get("chunk_count"),
    )
    if expected_chunks is None:
        remaining: int | None = None
        percent_loaded: float | None = None
        percent_remaining: float | None = None
        complete: bool | None = None
    else:
        remaining = max(expected_chunks - loaded_chunks, 0)
        if expected_chunks == 0:
            percent_loaded = 100.0
            percent_remaining = 0.0
        else:
            percent_loaded = round(
                100.0 * loaded_chunks / expected_chunks,
                1,
            )
            percent_remaining = round(
                100.0 * remaining / expected_chunks,
                1,
            )
        complete = loaded_chunks >= expected_chunks

    if expected_chunks is None:
        state = "unknown"
        verdict = (
            f"UNKNOWN - no expected chunk count; "
            f"Qdrant has {loaded_chunks} point(s)"
        )
    elif complete:
        state = "complete"
        verdict = (
            f"COMPLETE - {loaded_chunks} of {expected_chunks} "
            f"chunks are in Qdrant"
        )
    elif loaded_chunks > 0:
        state = "in_progress"
        verdict = (
            f"IN PROGRESS - {remaining} of {expected_chunks} chunks "
            f"({percent_remaining}%) still to load"
        )
    elif pending_chunks > 0:
        state = "queued"
        verdict = (
            f"QUEUED - 0 of {expected_chunks} chunks loaded; "
            f"the worker has not uploaded yet"
        )
    else:
        state = "not_loaded"
        verdict = (
            f"NOT LOADED - 0 of {expected_chunks} chunks loaded"
        )

    notes: list[str] = []
    if "full collection scan" in count_source:
        notes.append(
            "document_id payload index is missing; counts used a full "
            "scan. Run once with --create-index for faster checks."
        )
    if (
        expected_chunks is not None
        and loaded_chunks > expected_chunks
    ):
        notes.append(
            f"Qdrant has {loaded_chunks - expected_chunks} more points than "
            "the expected chunk count; old points may be left from a "
            "previous parse."
        )
    if (
        expected_chunks is not None
        and not complete
        and pending_chunks > 0
        and pending_chunks != remaining
    ):
        notes.append(
            f"Redis has {pending_chunks} pending chunks but Qdrant is only "
            f"missing {remaining}; the worker may be mid-batch."
        )
    if redis_status.get("status") in {"embedded", "already_indexed"} and not complete:
        notes.append(
            "Redis says the document is embedded, but Qdrant has fewer "
            "points than expected; the two stores are out of sync."
        )

    status = str(redis_status.get("status") or "")
    if not status and indexed:
        status = "indexed"

    return LoadReport(
        document_id=document_id,
        ticker=_text(meta, indexed, "ticker")
        or _ticker_from_document_id(document_id),
        company=_text(meta, indexed, "company"),
        form_type=_text(meta, indexed, "form_type"),
        report_date=_text(meta, indexed, "report_date"),
        accession_number=_text(meta, indexed, "accession_number"),
        expected_chunks=expected_chunks,
        loaded_chunks=loaded_chunks,
        pending_chunks=pending_chunks,
        percent_loaded=percent_loaded,
        remaining_chunks=remaining,
        percent_remaining=percent_remaining,
        complete=complete,
        state=state,
        verdict=verdict,
        count_source=count_source,
        redis_status=status,
        indexed_at=_text(meta, indexed, "indexed_at"),
        notes=tuple(notes),
    )


def build_report_for(
    hint: DocumentHint,
    chunk_store: ChunkStore,
    store: QdrantStore,
) -> LoadReport:
    document_id = hint.document_id
    status = _load_status(chunk_store, document_id)
    indexed = _load_indexed(chunk_store, document_id, hint.meta)
    pending = _pending_chunks(chunk_store, document_id)
    loaded, source = count_qdrant(store, document_id)
    return build_report(
        document_id=document_id,
        meta=hint.meta,
        redis_status=status,
        indexed=indexed,
        pending_chunks=pending,
        loaded_chunks=loaded,
        count_source=source,
    )


def document_reports(
    chunk_store: ChunkStore,
    store: QdrantStore,
    *,
    ticker: str | None = None,
    year: int | None = None,
    form: str | None = None,
    document_id: str | None = None,
    all_documents: bool = True,
) -> list[dict[str, Any]]:
    """Build JSON-ready load reports for matching documents.

    ``all_documents=False`` keeps only the latest match, mirroring the CLI
    default. An exact ``document_id`` is always reported on its own.
    """
    hints = load_documents(
        chunk_store,
        ticker=ticker,
        year=year,
        form=form,
        document_id=document_id,
    )
    if not hints:
        return []
    selected = hints if all_documents or document_id else hints[:1]
    return [
        build_report_for(hint, chunk_store, store).as_dict()
        for hint in selected
    ]


def _format_int(value: int | None) -> str:
    return "n/a" if value is None else str(value)


def _format_percent(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1f}%"


def render_report(report: LoadReport, *, timestamp: str) -> str:
    lines = [
        f"[{timestamp}] qdrant load status: {report.document_id}",
        f"  ticker          : {report.ticker or '-'}",
        f"  company         : {report.company or '-'}",
        (
            f"  filing          : {report.form_type or '-'} "
            f"report_date={report.report_date or '-'} "
            f"accession={report.accession_number or '-'}"
        ),
        f"  redis status    : {report.redis_status or '-'}",
        (
            f"  expected        : {_format_int(report.expected_chunks)} "
            "chunks"
        ),
        (
            f"  loaded          : {report.loaded_chunks} chunks "
            f"({_format_percent(report.percent_loaded)})"
        ),
        (
            f"  remaining       : {_format_int(report.remaining_chunks)} "
            f"chunks ({_format_percent(report.percent_remaining)})"
        ),
        (
            f"  redis pending   : {report.pending_chunks} chunks awaiting "
            "embed/upload"
        ),
        f"  qdrant count    : {report.loaded_chunks} via {report.count_source}",
        f"  verdict         : {report.verdict}",
    ]
    if report.indexed_at:
        lines.append(f"  indexed at      : {report.indexed_at}")
    for note in report.notes:
        lines.append(f"  note            : {note}")
    return "\n".join(lines)


def render_compact(report: LoadReport, *, timestamp: str) -> str:
    return (
        f"[{timestamp}] {report.document_id} "
        f"{report.loaded_chunks}/{_format_int(report.expected_chunks)} "
        f"({_format_percent(report.percent_loaded)}) "
        f"remaining={_format_int(report.remaining_chunks)} "
        f"pending={report.pending_chunks} "
        f"redis={report.redis_status or '-'} state={report.state}"
    )


def _emit(text: str, log_file: str | None) -> None:
    print(text)
    if log_file:
        with open(log_file, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def _selection_note(hints: list[DocumentHint], all_documents: bool) -> str:
    if len(hints) <= 1 or all_documents:
        return ""
    return (
        f"matched {len(hints)} documents; showing the latest. "
        "Use --year/--form to narrow or --all to list every match."
    )


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report how much of a filing is loaded into Qdrant and how "
            "many chunks are still missing."
        )
    )
    parser.add_argument("ticker", nargs="?", help="Ticker, e.g. NFLX")
    parser.add_argument(
        "--document-id",
        help="Exact document id, e.g. nflx_2025_10k",
    )
    parser.add_argument("--year", type=int, help="Report year, e.g. 2025")
    parser.add_argument(
        "--form",
        "--form-type",
        dest="form",
        help="Form type, e.g. 10-K",
    )
    parser.add_argument(
        "--all",
        dest="all_documents",
        action="store_true",
        help="Show every matching document instead of the latest",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Poll until the selected document is complete",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Seconds between checks in watch mode (default: 5)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object per document (JSONL in watch mode)",
    )
    parser.add_argument(
        "--log-file",
        help="Append every check to this file",
    )
    parser.add_argument(
        "--create-index",
        action="store_true",
        help=(
            "Create the document_id keyword payload index before counting "
            "(one-time schema change)"
        ),
    )
    parser.add_argument(
        "--collection",
        help="Override QDRANT_COLLECTION",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)
    if not args.ticker and not args.document_id:
        parser.error("provide a ticker or --document-id")
    if args.interval < 0.5:
        parser.error("--interval must be at least 0.5 seconds")
    return args


def _run(
    args: argparse.Namespace,
    chunk_store: ChunkStore,
    store: QdrantStore,
) -> int:
    first = True
    while True:
        hints = load_documents(
            chunk_store,
            ticker=args.ticker,
            year=args.year,
            form=args.form,
            document_id=args.document_id,
        )
        if not hints:
            print(
                "No matching document found. It may not have been chunked "
                "yet; check the loader/chunk worker logs.",
                file=sys.stderr,
            )
            return 2

        selected = hints if args.all_documents else hints[:1]
        reports = [
            build_report_for(hint, chunk_store, store)
            for hint in selected
        ]
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        complete = all(report.complete is True for report in reports)
        show_full = bool(first or not args.watch or complete)
        note = _selection_note(hints, args.all_documents)

        if args.json:
            lines = []
            for report in reports:
                payload = report.as_dict()
                payload["checked_at"] = timestamp
                if note:
                    payload["selection_note"] = note
                lines.append(json.dumps(payload, ensure_ascii=False))
            _emit("\n".join(lines), args.log_file)
        else:
            lines = []
            if note and show_full:
                lines.append(f"[{timestamp}] note: {note}")
            for report in reports:
                if show_full:
                    lines.append(
                        render_report(report, timestamp=timestamp)
                    )
                else:
                    lines.append(
                        render_compact(report, timestamp=timestamp)
                    )
            _emit("\n".join(lines), args.log_file)

        if not args.watch or complete:
            return 0 if complete else 1
        first = False
        time.sleep(args.interval)


def main(argv: Iterable[str] | None = None) -> int:
    load_dotenv(Path(__file__).resolve().parent / ".env")
    args = _parse_args(argv)
    if not os.getenv("QDRANT_URL"):
        print(
            "QDRANT_URL is not set; refusing to report against an "
            "in-memory Qdrant store.",
            file=sys.stderr,
        )
        return 2

    chunk_store = ChunkStore()
    store = (
        QdrantStore(collection_name=args.collection)
        if args.collection
        else QdrantStore()
    )
    try:
        if args.create_index:
            ensure_document_index(store)
        return _run(args, chunk_store, store)
    except KeyboardInterrupt:
        print("\nstopped", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    finally:
        store.close()
        chunk_store.close()


if __name__ == "__main__":
    raise SystemExit(main())
