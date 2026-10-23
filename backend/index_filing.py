"""CLI for parsing and indexing one SEC filing.

Examples:
    python index_filing.py AAPL --form 10-K --report-year 2025 --dry-run
    python index_filing.py --file dump/aapl.htm --ticker AAPL --dry-run
    python index_filing.py AAPL --form 10-K --report-year 2025 --recreate
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DeterministicHashEmbedder,
    EmbeddingClient,
)
from ingest_pipeline import (
    IngestSummary,
    default_document_id,
    ingest_filing_result,
    ingest_html,
)
from qdrant_store import QdrantStore
from sentence_records import (
    PARSER_VERSION,
    FilingMeta,
    ParsedFiling,
)
from sec_pipeline import fetch_filing

ENV_PATH = Path(__file__).resolve().parent / ".env"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse a filing, embed its records, and store them in Qdrant."
    )
    parser.add_argument("ticker", nargs="?", help="Ticker, e.g. AAPL")
    parser.add_argument("--file", type=Path, help="Use local HTML instead of fetching")
    parser.add_argument("--form", default="10-K", help="Form type (default: 10-K)")
    parser.add_argument("--report-year", type=int)
    parser.add_argument("--filing-year", type=int)
    parser.add_argument("--report-date", help="Report date, e.g. 2025-09-27")
    parser.add_argument("--document-id", help="Override the generated document id")
    parser.add_argument("--cik", default="", help="CIK for local-file mode")
    parser.add_argument("--accession", default="", help="Accession for local-file mode")
    parser.add_argument("--source-url", default="", help="Source URL for local-file mode")
    parser.add_argument("--collection", help="Qdrant collection name")
    parser.add_argument("--qdrant-url", help="Qdrant URL (or QDRANT_URL)")
    parser.add_argument("--qdrant-api-key", help="Qdrant API key (or QDRANT_API_KEY)")
    parser.add_argument("--qdrant-path", help="Local Qdrant storage path")
    parser.add_argument("--recreate", action="store_true", help="Recreate collection")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hash embed into in-memory Qdrant; no external services",
    )
    parser.add_argument("--samples", type=int, default=5, help="Records to print")
    parser.add_argument("--save-records", type=Path, help="Write JSONL + links + manifest")
    parser.add_argument(
        "--include-heading-records",
        action="store_true",
        help="Emit heading-only records in addition to context",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("EMBED_MODEL", DEFAULT_EMBEDDING_MODEL),
    )
    parser.add_argument("--batch-size", type=int, default=64)
    return parser


def _read_local(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _local_meta(args: argparse.Namespace, ticker: str) -> FilingMeta:
    return FilingMeta(
        document_id=args.document_id
        or default_document_id(ticker, args.form, args.report_date),
        ticker=ticker.upper(),
        cik=args.cik,
        form_type=args.form,
        accession_number=args.accession,
        source_url=args.source_url,
        filing_date=None,
        report_date=args.report_date,
    )


def _store(args: argparse.Namespace) -> QdrantStore:
    if args.dry_run:
        from qdrant_client import QdrantClient

        return QdrantStore(
            client=QdrantClient(location=":memory:"),
            collection_name=args.collection,
        )
    return QdrantStore(
        collection_name=args.collection,
        url=args.qdrant_url,
        api_key=args.qdrant_api_key,
        path=args.qdrant_path,
    )


def _embedder(args: argparse.Namespace):
    if args.dry_run:
        return DeterministicHashEmbedder()
    return EmbeddingClient(model=args.embedding_model, batch_size=args.batch_size)


def _write_outputs(
    path: Path, parsed: ParsedFiling, summary: IngestSummary
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for record in parsed.records:
            handle.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    links_path = path.with_name(f"{path.stem}.links.json")
    links_path.write_text(
        json.dumps(
            [asdict(link) for link in parsed.links],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    manifest_path = path.with_name(f"{path.stem}.manifest.json")
    manifest_path.write_text(
        json.dumps(
            {
                "meta": asdict(parsed.meta),
                "title": parsed.title,
                "parser_version": parsed.parser_version,
                "unit_counts": parsed.unit_counts,
                "anchor_count": len(parsed.anchor_targets),
                "summary": asdict(summary),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _print_summary(parsed: ParsedFiling, summary: IngestSummary, samples: int) -> None:
    print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
    for record in parsed.records[: max(samples, 0)]:
        hierarchy = " > ".join(record.hierarchy)
        print(f"[{record.order_index}] {record.unit_type} | {hierarchy}")
        print(record.text[:300].replace("\n", " "))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(ENV_PATH if ENV_PATH.exists() else None)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.embedding_model = args.embedding_model or DEFAULT_EMBEDDING_MODEL

    if args.file is None and not args.ticker:
        parser.error("ticker is required unless --file is used")
    if args.file is not None and not args.ticker:
        parser.error("--ticker is required with --file")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")

    store = _store(args)
    try:
        if args.file is not None:
            html = _read_local(args.file)
            meta = _local_meta(args, args.ticker)
            summary, parsed = ingest_html(
                html,
                meta,
                store,
                _embedder(args),
                embedding_model=args.embedding_model,
                recreate=args.recreate,
                include_heading_records=args.include_heading_records,
                parser_version=PARSER_VERSION,
                batch_size=args.batch_size,
            )
        else:
            result = fetch_filing(
                args.ticker,
                args.form,
                report_year=args.report_year,
                filing_year=args.filing_year,
            )
            summary, parsed = ingest_filing_result(
                result,
                store,
                _embedder(args),
                document_id=args.document_id,
                embedding_model=args.embedding_model,
                recreate=args.recreate,
                include_heading_records=args.include_heading_records,
                parser_version=PARSER_VERSION,
                batch_size=args.batch_size,
            )
        _print_summary(parsed, summary, args.samples)
        if args.save_records:
            _write_outputs(args.save_records, parsed, summary)
            print(f"saved records to {args.save_records}")
        return 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
