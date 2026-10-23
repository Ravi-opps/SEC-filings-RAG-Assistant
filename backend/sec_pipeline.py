"""End-to-end SEC filing fetch, extraction, and local dump pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from document_extractor import ExtractionResult, extract_document
from filing_resolver import FilingRef, find_filing
from sec_client import SecClient

DUMP_DIR = Path(__file__).resolve().parent / "dump"


@dataclass
class FilingResult:
    ticker: str
    cik: int
    company: str
    form_type: str
    accession_number: str
    filing_date: str
    report_date: str
    primary_document: str
    source_url: str
    content_type: str
    title: str
    extracted_text: str
    dump_path: str
    raw_content: bytes


def _safe_filename(ref: FilingRef, content_type: str) -> str:
    suffix = Path(ref.primary_document).suffix
    if not suffix:
        suffix = ".xml" if "xml" in content_type.lower() else ".html"
    stem = f"{ref.ticker}_{ref.form_type}_{ref.accession_number}"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    return f"{stem}{suffix}"


def save_document(
    dump_dir: Path,
    ref: FilingRef,
    raw: bytes,
    content_type: str,
) -> Path:
    dump_dir.mkdir(parents=True, exist_ok=True)
    path = dump_dir / _safe_filename(ref, content_type)
    path.write_bytes(raw)
    return path


def fetch_filing(
    ticker: str,
    form_type: str,
    report_year: int | None = None,
    filing_year: int | None = None,
    client: SecClient | None = None,
    dump_dir: Path | str | None = None,
    cache: Any | None = None,
) -> FilingResult:
    """Resolve, download, extract, and dump a single SEC filing document."""
    client = client or SecClient()
    ref = find_filing(
        client,
        ticker,
        form_type,
        report_year=report_year,
        filing_year=filing_year,
        cache=cache,
    )

    raw, content_type = client.get_document(ref.source_url)
    extraction: ExtractionResult = extract_document(
        raw, content_type, ref.source_url
    )
    path = save_document(
        Path(dump_dir) if dump_dir is not None else DUMP_DIR,
        ref,
        raw,
        content_type,
    )

    return FilingResult(
        ticker=ref.ticker,
        cik=ref.cik,
        company=ref.company,
        form_type=ref.form_type,
        accession_number=ref.accession_number,
        filing_date=ref.filing_date,
        report_date=ref.report_date,
        primary_document=ref.primary_document,
        source_url=ref.source_url,
        content_type=extraction.content_type,
        title=extraction.title,
        extracted_text=extraction.text,
        dump_path=str(path),
        raw_content=raw,
    )
