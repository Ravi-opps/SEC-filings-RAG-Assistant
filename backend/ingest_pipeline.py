"""Parse, embed, and store one SEC filing in Qdrant."""

from __future__ import annotations

import inspect
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from chunk_store import ChunkStore, indexed_document_key
from embeddings import (
    DEFAULT_EMBEDDING_MODEL,
    AsyncEmbedFn,
    AsyncEmbeddingClient,
    EmbeddingClient,
    EmbedFn,
)
from qdrant_store import AsyncQdrantStore, QdrantStore
from sec_pipeline import FilingResult
from sentence_records import (
    PARSER_VERSION,
    FilingMeta,
    ParsedFiling,
    parse_filing_html,
)


@dataclass(frozen=True)
class IngestSummary:
    document_id: str
    collection_name: str
    records: int
    paragraphs: int
    list_items: int
    tables: int
    headings: int
    links: int
    links_resolved: int
    points_upserted: int
    vector_size: int
    embedding_model: str
    parser_version: str
    year: int | None


def _slug(value: str, fallback: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "", value.lower())
    return slug or fallback


def default_document_id(
    ticker: str, form_type: str, report_date: str | None = None
) -> str:
    ticker_slug = _slug(ticker, "unknown")
    form_slug = _slug(form_type, "filing")
    year = (report_date or "")[:4]
    if year.isdigit():
        return f"{ticker_slug}_{year}_{form_slug}"
    return f"{ticker_slug}_{form_slug}"


def meta_from_filing_result(
    result: FilingResult,
    *,
    document_id: str | None = None,
) -> FilingMeta:
    return FilingMeta(
        document_id=document_id
        or default_document_id(
            result.ticker, result.form_type, result.report_date
        ),
        ticker=result.ticker.upper(),
        cik=str(result.cik),
        form_type=result.form_type,
        accession_number=result.accession_number,
        source_url=result.source_url,
        company=result.company,
        filing_date=result.filing_date,
        report_date=result.report_date,
    )


def _embed(
    embedder: EmbeddingClient | EmbedFn,
    texts: list[str],
) -> list[list[float]]:
    if hasattr(embedder, "embed_documents"):
        return embedder.embed_documents(texts)
    return embedder(texts)


async def _embed_async(
    embedder: AsyncEmbeddingClient | AsyncEmbedFn,
    texts: list[str],
) -> list[list[float]]:
    if hasattr(embedder, "embed_documents"):
        result = embedder.embed_documents(texts)
    else:
        result = embedder(texts)
    if inspect.isawaitable(result):
        result = await result
    return result


def _counts(records) -> dict[str, int]:
    counts: dict[str, int] = {}
    for record in records:
        counts[record.unit_type] = counts.get(record.unit_type, 0) + 1
    return counts


def _resolved_model(records, embedding_model: str | None) -> str:
    return (
        embedding_model
        or (records[0].embedding_model if records else "")
        or DEFAULT_EMBEDDING_MODEL
    )


def _summary(
    parsed: ParsedFiling,
    store: QdrantStore | AsyncQdrantStore,
    records,
    counts: dict[str, int],
    *,
    points: int,
    vector_size: int,
    embedding_model: str,
) -> IngestSummary:
    return IngestSummary(
        document_id=parsed.meta.document_id,
        collection_name=store.collection_name,
        records=len(records),
        paragraphs=counts.get("paragraph", 0),
        list_items=counts.get("list_item", 0),
        tables=counts.get("table", 0),
        headings=counts.get("heading", 0),
        links=len(parsed.links),
        links_resolved=sum(1 for link in parsed.links if link.resolved),
        points_upserted=points,
        vector_size=vector_size,
        embedding_model=embedding_model,
        parser_version=parsed.parser_version,
        year=records[0].year if records else None,
    )


def _indexed_payload(
    parsed: ParsedFiling,
    store: QdrantStore | AsyncQdrantStore,
    summary: IngestSummary,
) -> dict:
    meta = parsed.meta
    return {
        "document_id": meta.document_id,
        "ticker": meta.ticker,
        "cik": meta.cik,
        "company": meta.company,
        "form_type": meta.form_type,
        "accession_number": meta.accession_number,
        "source_url": meta.source_url,
        "filing_date": meta.filing_date,
        "report_date": meta.report_date,
        "collection_name": store.collection_name,
        "records": summary.records,
        "paragraphs": summary.paragraphs,
        "list_items": summary.list_items,
        "tables": summary.tables,
        "headings": summary.headings,
        "points_upserted": summary.points_upserted,
        "vector_size": summary.vector_size,
        "embedding_model": summary.embedding_model,
        "parser_version": summary.parser_version,
        "year": summary.year,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
    }


def index_parsed(
    parsed: ParsedFiling,
    store: QdrantStore,
    embedder: EmbeddingClient | EmbedFn | None = None,
    *,
    embedding_model: str | None = None,
    recreate: bool = False,
    batch_size: int = 64,
    wait: bool = True,
) -> IngestSummary:
    """Embed and upsert an already-parsed filing.

    Keeping this separate lets callers persist the parsed chunks before any
    embedding/network work is attempted.
    """
    records = parsed.records
    counts = _counts(records)
    resolved_model = _resolved_model(records, embedding_model)

    if records:
        embedder = embedder or EmbeddingClient(
            model=resolved_model, batch_size=batch_size
        )
        vectors = _embed(
            embedder,
            [record.embedding_text for record in records],
        )
        if len(vectors) != len(records):
            raise ValueError(
                f"embedder returned {len(vectors)} vectors for "
                f"{len(records)} records"
            )
        if not vectors or not vectors[0]:
            raise ValueError("embedder returned an empty vector")
        vector_size = len(vectors[0])
        store.ensure_collection(vector_size, recreate=recreate)
        points = store.upsert(records, vectors, wait=wait)
    else:
        vector_size = 0
        points = 0

    return _summary(
        parsed,
        store,
        records,
        counts,
        points=points,
        vector_size=vector_size,
        embedding_model=resolved_model,
    )


async def index_parsed_async(
    parsed: ParsedFiling,
    store: AsyncQdrantStore,
    embedder: AsyncEmbeddingClient | AsyncEmbedFn | None = None,
    chunk_store: ChunkStore | None = None,
    *,
    embedding_model: str | None = None,
    recreate: bool = False,
    embed_batch_size: int = 32,
    upload_batch_size: int = 32,
    wait: bool = True,
) -> IngestSummary:
    """Embed and upsert a filing natively on the running event loop.

    Embedding requests use ``httpx.AsyncClient`` and Qdrant writes use
    ``AsyncQdrantClient``. Both yield to the loop, so Kafka consumption and
    other documents continue while network and storage work is in flight.
    """
    if embed_batch_size < 1:
        raise ValueError("embed_batch_size must be at least 1")
    if upload_batch_size < 1:
        raise ValueError("upload_batch_size must be at least 1")

    records = parsed.records
    counts = _counts(records)
    resolved_model = _resolved_model(records, embedding_model)
    if not records:
        return _summary(
            parsed,
            store,
            records,
            counts,
            points=0,
            vector_size=0,
            embedding_model=resolved_model,
        )

    embedder = embedder or AsyncEmbeddingClient(
        model=resolved_model,
        batch_size=embed_batch_size,
    )
    vector_size = 0
    points = 0
    for start in range(0, len(records), embed_batch_size):
        batch_records = records[start : start + embed_batch_size]
        texts = [record.embedding_text for record in batch_records]
        vectors = await _embed_async(embedder, texts)
        if len(vectors) != len(batch_records):
            raise ValueError(
                f"embedder returned {len(vectors)} vectors for "
                f"{len(batch_records)} records"
            )
        if not vectors or not vectors[0]:
            raise ValueError("embedder returned an empty vector")
        batch_vector_size = len(vectors[0])
        if vector_size == 0:
            vector_size = batch_vector_size
            await store.ensure_collection(vector_size, recreate=recreate)
        elif batch_vector_size != vector_size:
            raise ValueError(
                f"embedding dimension changed from {vector_size} to "
                f"{batch_vector_size}"
            )
        points += await store.upsert(
            batch_records,
            vectors,
            batch_size=upload_batch_size,
            wait=wait,
        )
        if chunk_store is not None:
            await chunk_store.delete_chunks_async(
                parsed.meta.document_id,
                [record.point_id for record in batch_records],
            )

    summary = _summary(
        parsed,
        store,
        records,
        counts,
        points=points,
        vector_size=vector_size,
        embedding_model=resolved_model,
    )
    if chunk_store is not None:
        await chunk_store.set_indexed_async(
            indexed_document_key(
                parsed.meta.document_id,
                parsed.meta.accession_number,
            ),
            _indexed_payload(parsed, store, summary),
        )
    return summary


def ingest_html(
    html: str | bytes | None,
    meta: FilingMeta,
    store: QdrantStore,
    embedder: EmbeddingClient | EmbedFn | None = None,
    *,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    recreate: bool = False,
    include_heading_records: bool = False,
    parser_version: str = PARSER_VERSION,
    batch_size: int = 64,
) -> tuple[IngestSummary, ParsedFiling]:
    parsed = parse_filing_html(
        html,
        meta,
        embedding_model=embedding_model,
        parser_version=parser_version,
        include_heading_records=include_heading_records,
    )
    summary = index_parsed(
        parsed,
        store,
        embedder,
        embedding_model=embedding_model,
        recreate=recreate,
        batch_size=batch_size,
    )
    return summary, parsed


def ingest_filing_result(
    result: FilingResult,
    store: QdrantStore,
    embedder: EmbeddingClient | EmbedFn | None = None,
    *,
    document_id: str | None = None,
    **kwargs,
) -> tuple[IngestSummary, ParsedFiling]:
    meta = meta_from_filing_result(result, document_id=document_id)
    raw = result.raw_content
    if isinstance(raw, bytes):
        html = raw.decode("utf-8", errors="replace")
    else:
        html = raw or result.extracted_text
    return ingest_html(html, meta, store, embedder, **kwargs)
