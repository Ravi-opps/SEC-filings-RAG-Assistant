"""Kafka job queue for asynchronous filing downloads."""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

try:
    from aiokafka import AIOKafkaProducer
    from aiokafka.errors import KafkaError
except ImportError:  # pragma: no cover - aiokafka is a declared dependency
    AIOKafkaProducer = None  # type: ignore[assignment]

    class KafkaError(Exception):
        pass


DEFAULT_BOOTSTRAP_SERVERS = "localhost:9092"
DEFAULT_TOPIC = "sec-filings"
DEFAULT_STEP2_TOPIC = "sec-filings-index"


class QueueUnavailableError(Exception):
    """The Kafka job queue could not accept the job."""


def bootstrap_servers() -> str:
    return os.getenv("KAFKA_BOOTSTRAP_SERVERS") or DEFAULT_BOOTSTRAP_SERVERS


def topic_name() -> str:
    return os.getenv("KAFKA_TOPIC") or DEFAULT_TOPIC


def step2_topic_name() -> str:
    return os.getenv("KAFKA_STEP2_TOPIC") or DEFAULT_STEP2_TOPIC


_producer: Any | None = None
_producer_servers: str | None = None
_lock = asyncio.Lock()


async def get_producer() -> Any:
    global _producer, _producer_servers
    servers = bootstrap_servers()
    if _producer is not None and _producer_servers == servers:
        return _producer
    async with _lock:
        if _producer is None or _producer_servers != servers:
            if AIOKafkaProducer is None:
                raise QueueUnavailableError("aiokafka is not installed")
            producer = AIOKafkaProducer(
                bootstrap_servers=servers,
                acks="all",
                enable_idempotence=True,
                request_timeout_ms=5000,
            )
            try:
                await producer.start()
            except KafkaError as exc:
                raise QueueUnavailableError(
                    f"cannot connect to Kafka at {servers}: {exc}"
                ) from exc
            _producer = producer
            _producer_servers = servers
    return _producer


async def publish_job(job: dict, topic: str | None = None) -> None:
    """Publish one job to ``topic``, waiting until Kafka acknowledges it."""
    producer = await get_producer()
    target = topic or topic_name()
    payload = json.dumps(job, ensure_ascii=False).encode("utf-8")
    key = str(job.get("ticker") or job.get("document_id") or "").encode("utf-8")
    try:
        await producer.send_and_wait(target, payload, key=key)
    except KafkaError as exc:
        raise QueueUnavailableError(
            f"failed to publish job to {target}: {exc}"
        ) from exc


async def publish_filing_job(job: dict) -> None:
    """Publish a download job for the ``loader`` consumer group."""
    await publish_job(job, topic_name())


async def publish_index_job(job: dict) -> None:
    """Publish a step-2 job for the ``step2`` consumer group."""
    await publish_job(job, step2_topic_name())


async def close_producer() -> None:
    global _producer, _producer_servers
    if _producer is not None:
        try:
            await _producer.stop()
        finally:
            _producer = None
            _producer_servers = None
