"""Kafka step-2 worker for structure-aware SEC chunking.

Run with::

    python chunk_worker.py

The worker consumes ``KAFKA_STEP2_TOPIC``, parses up to ``CHUNK_PROCESSES``
documents concurrently, persists their structure-aware chunks in Redis, and
then embeds and upserts those chunks into Qdrant. Redis chunk records are
deleted as each Qdrant batch is upserted, and already-indexed documents are
skipped until their embedding model or parser version changes. Kafka offsets
are committed only after the whole batch succeeds.

Parsing and chunking run in a ``ProcessPoolExecutor`` so the GIL is not shared
between documents. Embedding/network work remains in the parent process.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import logging
import multiprocessing
import os
from concurrent.futures import Future, ProcessPoolExecutor
from pathlib import Path

from aiokafka import AIOKafkaConsumer
from dotenv import load_dotenv

from chunk_store import ChunkStore, indexed_document_key
from embeddings import AsyncEmbeddingClient, DEFAULT_EMBEDDING_MODEL
from ingest_pipeline import IngestSummary, default_document_id, index_parsed_async
from job_queue import bootstrap_servers, step2_topic_name
from qdrant_store import AsyncQdrantStore
from sentence_records import (
    PARSER_VERSION,
    FilingMeta,
    ParsedFiling,
    parse_filing_html,
)

load_dotenv(Path(__file__).resolve().parent / ".env")

LOGGER = logging.getLogger("chunk_worker")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, value)


GROUP_ID = os.getenv("KAFKA_STEP2_GROUP_ID") or "step2"
WORKERS = _env_int("CHUNK_PROCESSES", 3)
MAX_RETRIES = _env_int("CHUNK_MAX_RETRIES", 3)
RETRY_BACKOFF_SECONDS = _env_float("CHUNK_RETRY_BACKOFF_SECONDS", 5.0)
POLL_TIMEOUT_MS = _env_int("CHUNK_POLL_TIMEOUT_MS", 1000)
MAX_POLL_INTERVAL_MS = _env_int("KAFKA_MAX_POLL_INTERVAL_MS", 1800000)
EMBED_BATCH_SIZE = _env_int("CHUNK_EMBED_BATCH_SIZE", 32)
UPLOAD_BATCH_SIZE = _env_int("CHUNK_UPLOAD_BATCH_SIZE", 32)

_WORKER_CHUNK_STORE: ChunkStore | None = None
_WORKER_EMBEDDING_MODEL = DEFAULT_EMBEDDING_MODEL


def _init_worker(redis_url: str, ttl: int, embedding_model: str) -> None:
    global _WORKER_CHUNK_STORE, _WORKER_EMBEDDING_MODEL
    _WORKER_CHUNK_STORE = ChunkStore(redis_url, ttl=ttl)
    _WORKER_EMBEDDING_MODEL = embedding_model
    atexit.register(_close_worker)


def _close_worker() -> None:
    global _WORKER_CHUNK_STORE
    if _WORKER_CHUNK_STORE is not None:
        _WORKER_CHUNK_STORE.close()
        _WORKER_CHUNK_STORE = None


def _read_html(path: Path) -> str:
    raw = path.read_bytes()
    for encoding in ("utf-8", "utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def meta_from_job(payload: dict) -> FilingMeta:
    ticker = str(payload.get("ticker") or "").upper()
    form_type = str(payload.get("form_type") or "10-K")
    report_date = payload.get("report_date")
    return FilingMeta(
        document_id=str(
            payload.get("document_id")
            or default_document_id(ticker, form_type, report_date)
        ),
        ticker=ticker,
        cik=str(payload.get("cik") or ""),
        form_type=form_type,
        accession_number=str(payload.get("accession_number") or ""),
        source_url=str(payload.get("source_url") or ""),
        company=payload.get("company"),
        filing_date=payload.get("filing_date"),
        report_date=report_date,
    )


def chunk_document(payload: dict) -> ParsedFiling:
    chunk_store = _WORKER_CHUNK_STORE or ChunkStore()
    saved_path = payload.get("saved_path")
    if not saved_path:
        raise ValueError("job payload is missing saved_path")
    path = Path(str(saved_path))
    if not path.is_file():
        raise FileNotFoundError(f"saved filing does not exist: {path}")
    source_type = str(payload.get("source_type") or "sec")
    parsed = parse_filing_html(
        _read_html(path),
        meta_from_job(payload),
        embedding_model=_WORKER_EMBEDDING_MODEL,
    )
    chunk_store.save_parsed(parsed, source_type=source_type)
    return parsed


def process_message(
    payload: dict,
    *,
    parse_pool: ProcessPoolExecutor,
) -> Future:
    return parse_pool.submit(chunk_document, payload)


def decode_message(message) -> dict:
    payload = json.loads(message.value)
    if not isinstance(payload, dict):
        raise ValueError("job payload is not a JSON object")
    return payload


async def process_batch(
    messages: list,
    *,
    parse_pool: ProcessPoolExecutor,
    chunk_store: ChunkStore,
    store: AsyncQdrantStore,
    embedder: AsyncEmbeddingClient,
) -> list[IngestSummary]:
    payloads = [decode_message(message) for message in messages]

    async def process_one(payload: dict) -> IngestSummary | None:
        meta = meta_from_job(payload)
        cache_key = indexed_document_key(
            meta.document_id,
            meta.accession_number,
        )
        cached = await chunk_store.get_indexed_async(cache_key)
        if cached is not None:
            try:
                expected = max(0, int(cached.get("points_upserted") or 0))
            except (TypeError, ValueError):
                expected = -1
            document_id = str(cached.get("document_id") or meta.document_id)
            accession_number = str(
                cached.get("accession_number") or meta.accession_number or ""
            )
            actual = await store.count(
                document_id=document_id,
                accession_number=accession_number or None,
            )
            current_model = str(cached.get("embedding_model") or "")
            current_parser = str(cached.get("parser_version") or "")
            current_collection = str(cached.get("collection_name") or "")
            if (
                expected >= 0
                and actual >= expected
                and bool(document_id)
                and current_model == embedder.model
                and current_parser == PARSER_VERSION
                and current_collection == store.collection_name
            ):
                LOGGER.info(
                    "skipping already indexed %s (%d points)",
                    document_id,
                    actual,
                )
                await chunk_store.mark_status_async(
                    document_id,
                    "already_indexed",
                    points_upserted=actual,
                    embedding_model=current_model,
                    parser_version=current_parser,
                )
                return None
            await chunk_store.delete_indexed_async(cache_key)

        parse_future = process_message(
            payload,
            parse_pool=parse_pool,
        )
        parsed = await asyncio.wrap_future(parse_future)
        summary = await index_parsed_async(
            parsed,
            store,
            embedder,
            chunk_store=chunk_store,
            embed_batch_size=EMBED_BATCH_SIZE,
            upload_batch_size=UPLOAD_BATCH_SIZE,
            wait=True,
        )
        await chunk_store.mark_status_async(
            parsed.meta.document_id,
            "embedded",
            points_upserted=summary.points_upserted,
            vector_size=summary.vector_size,
            embedding_model=summary.embedding_model,
            parser_version=summary.parser_version,
        )
        return summary

    results = await asyncio.gather(
        *(process_one(payload) for payload in payloads),
        return_exceptions=True,
    )
    errors = [result for result in results if isinstance(result, BaseException)]
    if errors:
        details = "; ".join(repr(error) for error in errors)
        raise RuntimeError(f"{len(errors)} of {len(results)} jobs failed: {details}")
    return [result for result in results if isinstance(result, IngestSummary)]


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    embedding_model = os.getenv("EMBED_MODEL") or DEFAULT_EMBEDDING_MODEL
    chunk_store = ChunkStore()
    store = AsyncQdrantStore()
    embedder = AsyncEmbeddingClient(
        model=embedding_model,
        batch_size=EMBED_BATCH_SIZE,
    )
    parse_pool = ProcessPoolExecutor(
        max_workers=WORKERS,
        mp_context=multiprocessing.get_context("spawn"),
        initializer=_init_worker,
        initargs=(chunk_store.url, chunk_store.ttl, embedding_model),
    )
    consumer = AIOKafkaConsumer(
        step2_topic_name(),
        bootstrap_servers=bootstrap_servers(),
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=False,
        max_poll_interval_ms=MAX_POLL_INTERVAL_MS,
    )
    try:
        await consumer.start()
        LOGGER.info(
            "chunk worker consuming topic=%s group=%s brokers=%s processes=%d",
            step2_topic_name(),
            GROUP_ID,
            bootstrap_servers(),
            WORKERS,
        )
        while True:
            batches = await consumer.getmany(
                timeout_ms=POLL_TIMEOUT_MS,
                max_records=WORKERS,
            )
            messages = [
                message
                for partition_messages in batches.values()
                for message in partition_messages
            ]
            if not messages:
                continue
            for attempt in range(1, MAX_RETRIES + 1):
                try:
                    summaries = await process_batch(
                        messages,
                        parse_pool=parse_pool,
                        chunk_store=chunk_store,
                        store=store,
                        embedder=embedder,
                    )
                    await consumer.commit()
                    for summary in summaries:
                        LOGGER.info(
                            "indexed %s chunks=%d points=%d",
                            summary.document_id,
                            summary.records,
                            summary.points_upserted,
                        )
                    break
                except Exception:
                    if attempt >= MAX_RETRIES:
                        LOGGER.exception(
                            "batch failed after %d attempts; offsets left uncommitted",
                            MAX_RETRIES,
                        )
                        raise
                    delay = RETRY_BACKOFF_SECONDS * attempt
                    LOGGER.exception(
                        "batch failed attempt %d/%d; retrying in %.1fs",
                        attempt,
                        MAX_RETRIES,
                        delay,
                    )
                    await asyncio.sleep(delay)
    finally:
        await consumer.stop()
        parse_pool.shutdown(wait=True)
        await store.close()
        await embedder.close()
        await chunk_store.close_async()
        chunk_store.close()


if __name__ == "__main__":
    asyncio.run(main())
