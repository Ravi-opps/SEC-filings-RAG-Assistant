"""Kafka worker that resolves filing URLs and downloads SEC HTML documents.

Run with::

    python loader_worker.py

The consumer group is ``loader``; every job is processed asynchronously and
the raw filing is written to ``dump/``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from aiokafka import AIOKafkaConsumer
from dotenv import load_dotenv

from async_sec import AsyncSecClient, find_filing_async
from job_queue import bootstrap_servers, publish_index_job, topic_name
from sec_pipeline import DUMP_DIR, save_document
from terminal_ui import DownloadUI

load_dotenv(Path(__file__).resolve().parent / ".env")

LOGGER = logging.getLogger("loader")

GROUP_ID = os.getenv("KAFKA_GROUP_ID") or "loader"


def dump_dir() -> Path:
    configured = os.getenv("DUMP_DIR")
    return Path(configured) if configured else DUMP_DIR


async def process_job(
    payload: dict, *, show_progress: bool | None = None
) -> Path:
    """Resolve the filing URL, download the document, and save it."""
    ticker = str(payload["ticker"])
    cik = int(payload["cik"])
    company = str(payload.get("company") or "")
    form_type = str(payload.get("form_type") or "10-K")
    report_year = payload.get("report_year")
    filing_year = payload.get("filing_year")

    client = AsyncSecClient()
    ui = DownloadUI(f"{ticker} {form_type}", enabled=show_progress)
    try:
        ui.start()
        try:
            ui.status("resolving")
            ref = await find_filing_async(
                client,
                ticker,
                form_type,
                report_year=int(report_year) if report_year is not None else None,
                filing_year=int(filing_year) if filing_year is not None else None,
                cik=cik,
                company=company,
            )
            if not ui.enabled:
                LOGGER.info("resolved %s %s -> %s", ticker, form_type, ref.source_url)

            ui.status("downloading")
            raw, content_type = await client.get_document(
                ref.source_url, on_progress=ui.update
            )
            path = await asyncio.to_thread(
                save_document, dump_dir(), ref, raw, content_type
            )
            ui.finish(f"{ref.accession_number} - {len(raw):,} bytes")
        except Exception:
            ui.fail()
            raise
        finally:
            ui.close()

        LOGGER.info(
            "downloaded %s %s (%d bytes) -> %s",
            ticker,
            ref.accession_number,
            len(raw),
            path,
        )

        index_job = {
            "stage": "index",
            "ticker": ticker,
            "cik": ref.cik,
            "company": ref.company or company,
            "form_type": ref.form_type,
            "accession_number": ref.accession_number,
            "filing_date": ref.filing_date,
            "report_date": ref.report_date,
            "primary_document": ref.primary_document,
            "source_url": ref.source_url,
            "saved_path": str(path),
            "content_type": content_type,
            "bytes": len(raw),
            "report_year": report_year,
            "filing_year": filing_year,
            "downloaded_at": datetime.now(timezone.utc).isoformat(),
        }
        if payload.get("requested_at") is not None:
            index_job["requested_at"] = payload["requested_at"]
        await publish_index_job(index_job)
        LOGGER.info("queued step2 job for %s %s", ticker, ref.accession_number)
        return path
    finally:
        await client.aclose()


async def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    consumer = AIOKafkaConsumer(
        topic_name(),
        bootstrap_servers=bootstrap_servers(),
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    await consumer.start()
    LOGGER.info(
        "loader consuming topic=%s group=%s brokers=%s",
        topic_name(),
        GROUP_ID,
        bootstrap_servers(),
    )
    try:
        async for message in consumer:
            try:
                payload = json.loads(message.value)
                if not isinstance(payload, dict):
                    raise ValueError("job payload is not a JSON object")
                await process_job(payload)
            except Exception:
                LOGGER.exception(
                    "failed to process offset=%s partition=%s",
                    message.offset,
                    message.partition,
                )
    finally:
        await consumer.stop()


if __name__ == "__main__":
    asyncio.run(main())
