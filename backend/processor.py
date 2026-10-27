"""Directory ingestion for HTML filings.

Pipeline for every HTML file:
    parse -> embed -> upsert to Qdrant

Parsed chunks are held in memory only; they are never written to disk.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from dotenv import load_dotenv

from embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    DeterministicHashEmbedder,
    EmbeddingClient,
    EmbedFn,
)
from ingest_pipeline import IngestSummary, index_parsed
from qdrant_store import QdrantStore
from sentence_records import (
    PARSER_VERSION,
    FilingMeta,
    ParsedFiling,
    parse_filing_html,
)

ENV_PATH = Path(__file__).resolve().parent / ".env"
HTML_SUFFIXES = {".html", ".htm"}

LOGGER = logging.getLogger("processor")


@dataclass(frozen=True)
class ProcessedDocument:
    source_path: str
    document_id: str
    records: int
    points_upserted: int
    links: int
    links_resolved: int


@dataclass
class RunSummary:
    files_seen: int = 0
    files_processed: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    records: int = 0
    points_upserted: int = 0
    documents: list[ProcessedDocument] = field(default_factory=list)
    failures: list[dict[str, str]] = field(default_factory=list)

    def merge(self, other: "RunSummary") -> None:
        self.files_seen += other.files_seen
        self.files_processed += other.files_processed
        self.files_skipped += other.files_skipped
        self.files_failed += other.files_failed
        self.records += other.records
        self.points_upserted += other.points_upserted
        self.documents.extend(other.documents)
        self.failures.extend(other.failures)


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_") or "document"


def _read_html(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def meta_for_file(
    path: Path,
    *,
    document_id: str | None = None,
    ticker: str = "",
    cik: str = "",
    form_type: str = "HTML",
    accession_number: str = "",
    source_url: str | None = None,
) -> FilingMeta:
    """Build deterministic metadata for a standalone HTML file."""
    return FilingMeta(
        document_id=document_id or _slug(path.stem),
        ticker=ticker.upper(),
        cik=cik,
        form_type=form_type,
        accession_number=accession_number,
        source_url=source_url if source_url is not None else str(path),
    )


def process_html(
    html: str | bytes,
    *,
    source_type: str,
    meta: FilingMeta,
    store: QdrantStore,
    embedder: EmbeddingClient | EmbedFn | None = None,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = 64,
    include_heading_records: bool = False,
    recreate: bool = False,
    wait: bool = True,
) -> tuple[IngestSummary, ParsedFiling]:
    """Parse one HTML document, then embed and upsert it in memory."""
    parsed = parse_filing_html(
        html,
        meta,
        embedding_model=embedding_model,
        parser_version=PARSER_VERSION,
        include_heading_records=include_heading_records,
    )
    for record in parsed.records:
        record.source_type = source_type

    summary = index_parsed(
        parsed,
        store,
        embedder,
        embedding_model=embedding_model,
        recreate=recreate,
        batch_size=batch_size,
        wait=wait,
    )
    return summary, parsed


def process_html_file(
    path: Path,
    *,
    source_type: str,
    store: QdrantStore,
    embedder: EmbeddingClient | EmbedFn | None = None,
    document_id: str | None = None,
    ticker: str = "",
    cik: str = "",
    form_type: str = "HTML",
    accession_number: str = "",
    source_url: str | None = None,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    batch_size: int = 64,
    include_heading_records: bool = False,
) -> tuple[IngestSummary, ParsedFiling]:
    meta = meta_for_file(
        path,
        document_id=document_id,
        ticker=ticker,
        cik=cik,
        form_type=form_type,
        accession_number=accession_number,
        source_url=source_url,
    )
    return process_html(
        _read_html(path),
        source_type=source_type,
        meta=meta,
        store=store,
        embedder=embedder,
        embedding_model=embedding_model,
        batch_size=batch_size,
        include_heading_records=include_heading_records,
    )


def process_directory(
    directory: str | Path,
    source_type: str,
    *,
    store: QdrantStore,
    embedder: EmbeddingClient | EmbedFn | None = None,
    **file_options,
) -> RunSummary:
    """Process every HTML file directly inside *directory*."""
    directory = Path(directory)
    files = sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in HTML_SUFFIXES
    )
    summary = RunSummary(files_seen=len(files))
    LOGGER.info("found %s HTML files in %s", len(files), directory)

    for path in files:
        try:
            result = process_html_file(
                path,
                source_type=source_type,
                store=store,
                embedder=embedder,
                **file_options,
            )
            file_summary, parsed = result
            if not parsed.records:
                summary.files_skipped += 1
                LOGGER.warning("no chunks in %s - skipped", path)
                continue
            summary.files_processed += 1
            summary.records += file_summary.records
            summary.points_upserted += file_summary.points_upserted
            summary.documents.append(
                ProcessedDocument(
                    source_path=str(path),
                    document_id=file_summary.document_id,
                    records=file_summary.records,
                    points_upserted=file_summary.points_upserted,
                    links=file_summary.links,
                    links_resolved=file_summary.links_resolved,
                )
            )
        except Exception as exc:  # one bad file must not stop the directory
            summary.files_failed += 1
            summary.failures.append({"file": str(path), "error": str(exc)})
            LOGGER.exception("failed to process %s", path)

    return summary


def _infer_source_type(directory: Path) -> str:
    name = directory.name.lower()
    if "true" in name:
        return "true"
    if "noisy" in name:
        return "noisy"
    return "general"


def run_ingestion(
    base_dir: str | Path,
    source_type: str | None = None,
    *,
    store: QdrantStore,
    embedder: EmbeddingClient | EmbedFn | None = None,
    wipe: bool = False,
    **file_options,
) -> RunSummary:
    """Scan *base_dir* and ingest HTML files by source-type sub-folder."""
    base = Path(base_dir)
    if not base.exists():
        raise FileNotFoundError(f"path does not exist: {base}")
    if not base.is_dir():
        raise NotADirectoryError(f"path is not a directory: {base}")

    if wipe:
        store.wipe()
        LOGGER.info("wiped collection %s", store.collection_name)

    subdirs = sorted(path for path in base.iterdir() if path.is_dir())
    summary = RunSummary()

    if not subdirs:
        resolved = source_type or _infer_source_type(base)
        summary.merge(
            process_directory(
                base,
                resolved,
                store=store,
                embedder=embedder,
                **file_options,
            )
        )
        return summary

    for subdir in subdirs:
        resolved = (
            "true"
            if "true" in subdir.name.lower()
            else "noisy"
            if "noisy" in subdir.name.lower()
            else subdir.name
        )
        summary.merge(
            process_directory(
                subdir,
                resolved,
                store=store,
                embedder=embedder,
                **file_options,
            )
        )
    return summary


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Parse HTML files, embed, and upsert into Qdrant."
    )
    parser.add_argument("target_dir", nargs="?", default="DATA")
    parser.add_argument("source_type", nargs="?", default=None)
    parser.add_argument("--collection", help="Qdrant collection name")
    parser.add_argument("--qdrant-url", help="Qdrant URL (or QDRANT_URL)")
    parser.add_argument("--qdrant-api-key", help="Qdrant API key (or QDRANT_API_KEY)")
    parser.add_argument("--qdrant-path", help="Local Qdrant storage path")
    parser.add_argument("--wipe", action="store_true", help="Drop the collection first")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Hash embeddings + in-memory Qdrant; no external services",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("EMBED_MODEL", DEFAULT_EMBEDDING_MODEL),
    )
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--include-heading-records", action="store_true")
    parser.add_argument("--ticker", default="")
    parser.add_argument("--cik", default="")
    parser.add_argument("--form", default="HTML")
    parser.add_argument("--accession", default="")
    parser.add_argument("--source-url", default=None)
    parser.add_argument("--document-id", default=None)
    parser.add_argument("--verbose", action="store_true")
    return parser


def _build_store(args: argparse.Namespace) -> QdrantStore:
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


def _build_embedder(args: argparse.Namespace):
    if args.dry_run:
        return DeterministicHashEmbedder()
    return EmbeddingClient(
        model=args.embedding_model,
        batch_size=args.batch_size,
    )


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    load_dotenv(ENV_PATH if ENV_PATH.exists() else None)
    parser = build_parser()
    args = parser.parse_args(argv)
    args.embedding_model = args.embedding_model or DEFAULT_EMBEDDING_MODEL
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    target = Path(args.target_dir)
    if not target.exists():
        print(f"error: path does not exist: {target}", file=sys.stderr)
        return 1
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")

    store = _build_store(args)
    try:
        summary = run_ingestion(
            target,
            args.source_type,
            store=store,
            embedder=_build_embedder(args),
            wipe=args.wipe,
            document_id=args.document_id,
            ticker=args.ticker,
            cik=args.cik,
            form_type=args.form,
            accession_number=args.accession,
            source_url=args.source_url,
            embedding_model=args.embedding_model,
            batch_size=args.batch_size,
            include_heading_records=args.include_heading_records,
        )
        print(json.dumps(asdict(summary), ensure_ascii=False, indent=2))
        return 1 if summary.files_failed else 0
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
