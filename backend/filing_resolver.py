"""Resolve tickers to CIKs and select SEC filings from submissions metadata."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from cache import ticker_cache_key
from sec_client import ARCHIVE_BASE, SecClient

ACCESSION_RE = re.compile(r"^\d{10}-\d{2}-\d{6}$")
HISTORY_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*\.json$")
REQUIRED_ARRAYS = (
    "accessionNumber",
    "filingDate",
    "reportDate",
    "form",
    "primaryDocument",
)


class ResolverError(Exception):
    """Base class for filing resolution failures."""


class TickerNotFoundError(ResolverError):
    """The ticker is not present in SEC company_tickers.json."""


class FilingNotFoundError(ResolverError):
    """No filing matches the requested form and period."""


class MalformedMetadataError(ResolverError):
    """SEC metadata is missing required fields or has invalid values."""


@dataclass(frozen=True)
class FilingRef:
    ticker: str
    cik: int
    company: str
    form_type: str
    accession_number: str
    filing_date: str
    report_date: str
    primary_document: str
    source_url: str


def normalize_ticker(ticker: str) -> str:
    return ticker.strip().upper()


def normalize_form(form_type: str) -> str:
    return form_type.strip().upper().replace(" ", "")


def parse_cached_company(
    cached: Any,
) -> tuple[int, str, int | None] | None:
    """Validate a cached ticker resolution, returning (cik, company, year)."""
    if not isinstance(cached, dict):
        return None
    try:
        cik = int(cached["cik"])
    except (KeyError, TypeError, ValueError):
        return None
    if cik <= 0:
        return None
    year = cached.get("year")
    try:
        year = int(year) if year is not None else None
    except (TypeError, ValueError):
        year = None
    return cik, str(cached.get("company") or ""), year


def company_from_mapping(mapping: Any, wanted: str) -> tuple[int, str]:
    """Extract a CIK and company name from SEC company_tickers.json."""
    if not isinstance(mapping, dict):
        raise MalformedMetadataError("SEC ticker map is not a JSON object")

    for entry in mapping.values():
        if not isinstance(entry, dict):
            continue
        if str(entry.get("ticker", "")).strip().upper() != wanted:
            continue
        try:
            cik = int(entry.get("cik_str"))
        except (TypeError, ValueError) as exc:
            raise MalformedMetadataError(
                f"Invalid CIK for ticker {wanted}"
            ) from exc
        if cik <= 0:
            raise MalformedMetadataError(f"Invalid CIK for ticker {wanted}")
        return cik, str(entry.get("title", ""))

    raise TickerNotFoundError(f"Ticker {wanted!r} was not found in SEC metadata")


def resolve_company(
    ticker: str,
    client: SecClient,
    cache: Any | None = None,
    year: int | None = None,
) -> tuple[int, str]:
    wanted = normalize_ticker(ticker)
    if not wanted:
        raise TickerNotFoundError("Ticker must not be empty")

    if cache is not None:
        cached = parse_cached_company(cache.get_json(ticker_cache_key(wanted)))
        if cached is not None:
            return cached[0], cached[1]

    cik, company = company_from_mapping(client.get_ticker_map(), wanted)
    if cache is not None:
        cache.set_json(
            ticker_cache_key(wanted),
            {"cik": cik, "company": company, "year": year},
        )
    return cik, company


def build_archive_url(
    cik: int | str, accession_number: str, primary_document: str
) -> str:
    """Build an EDGAR archive URL from validated SEC metadata.

    The archive path uses the CIK without leading zeroes and the accession
    number without hyphens. The primary document is quoted per path segment.
    """
    try:
        cik_int = int(cik)
    except (TypeError, ValueError) as exc:
        raise MalformedMetadataError(f"Invalid CIK: {cik!r}") from exc
    if cik_int <= 0:
        raise MalformedMetadataError(f"Invalid CIK: {cik!r}")

    accession = str(accession_number).strip()
    if not ACCESSION_RE.match(accession):
        raise MalformedMetadataError(
            f"Invalid accession number: {accession!r}"
        )

    document = str(primary_document).strip()
    if (
        not document
        or document.startswith(("http://", "https://", "//", "/"))
        or ".." in document
        or "\\" in document
        or "\x00" in document
    ):
        raise MalformedMetadataError(
            f"Invalid primary document: {document!r}"
        )

    safe_document = "/".join(quote(segment) for segment in document.split("/"))
    return f"{ARCHIVE_BASE}/{cik_int}/{accession.replace('-', '')}/{safe_document}"


def _unwrap_block(block: dict) -> dict:
    if not isinstance(block, dict):
        raise MalformedMetadataError("SEC submissions block is not an object")
    if all(name in block for name in REQUIRED_ARRAYS):
        return block

    recent = block.get("recent")
    if isinstance(recent, dict):
        return recent

    filings = block.get("filings")
    if isinstance(filings, dict):
        nested = filings.get("recent")
        if isinstance(nested, dict):
            return nested
    return block


def _rows_from_block(block: dict) -> list[dict[str, str]]:
    block = _unwrap_block(block)

    arrays: dict[str, list] = {}
    for name in REQUIRED_ARRAYS:
        value = block.get(name)
        if not isinstance(value, list):
            raise MalformedMetadataError(
                f"SEC submissions metadata is missing array {name!r}"
            )
        arrays[name] = value

    lengths = {len(values) for values in arrays.values()}
    if len(lengths) != 1:
        raise MalformedMetadataError(
            "SEC submissions arrays have inconsistent lengths"
        )

    count = lengths.pop()
    rows = []
    for index in range(count):
        rows.append(
            {
                name: (
                    str(arrays[name][index])
                    if arrays[name][index] is not None
                    else ""
                )
                for name in REQUIRED_ARRAYS
            }
        )
    return rows


def _year_matches(value: str, year: int) -> bool:
    return bool(value) and value[:4] == str(year)


def _select_rows(
    rows: list[dict[str, str]],
    form_type: str,
    report_year: int | None,
    filing_year: int | None,
) -> list[dict[str, str]]:
    matches = []
    for row in rows:
        if normalize_form(row["form"]) != form_type:
            continue
        if report_year is not None and not _year_matches(
            row["reportDate"], report_year
        ):
            continue
        if filing_year is not None and not _year_matches(
            row["filingDate"], filing_year
        ):
            continue
        matches.append(row)

    matches.sort(
        key=lambda row: (row["filingDate"], row["accessionNumber"]),
        reverse=True,
    )
    return matches


def _build_ref(
    ticker: str,
    cik: int,
    company: str,
    form_type: str,
    row: dict[str, str],
) -> FilingRef:
    accession = row["accessionNumber"].strip()
    document = row["primaryDocument"].strip()
    filing_date = row["filingDate"].strip()
    report_date = row["reportDate"].strip()

    if not filing_date:
        raise MalformedMetadataError("Selected filing is missing filingDate")
    if not document:
        raise MalformedMetadataError("Selected filing is missing primaryDocument")

    return FilingRef(
        ticker=normalize_ticker(ticker),
        cik=cik,
        company=company,
        form_type=form_type,
        accession_number=accession,
        filing_date=filing_date,
        report_date=report_date,
        primary_document=document,
        source_url=build_archive_url(cik, accession, document),
    )


def find_filing(
    client: SecClient,
    ticker: str,
    form_type: str,
    report_year: int | None = None,
    filing_year: int | None = None,
    cache: Any | None = None,
) -> FilingRef:
    """Find the latest filing matching the form and period criteria.

    ``report_year`` matches the fiscal/report period year (``reportDate``).
    ``filing_year`` matches the calendar year the filing was submitted
    (``filingDate``). They are distinct and are never substituted for each
    other. With no year supplied, the latest matching filing date wins.
    """
    cik, company = resolve_company(
        ticker,
        client,
        cache=cache,
        year=report_year if report_year is not None else filing_year,
    )
    wanted_form = normalize_form(form_type)
    submissions = client.get_submissions(cik)

    filings = submissions.get("filings")
    if not isinstance(filings, dict):
        raise MalformedMetadataError(
            "SEC submissions response is missing 'filings'"
        )

    recent = filings.get("recent")
    if not isinstance(recent, dict):
        raise MalformedMetadataError(
            "SEC submissions response is missing 'filings.recent'"
        )

    matches = _select_rows(
        _rows_from_block(recent), wanted_form, report_year, filing_year
    )
    if matches:
        return _build_ref(ticker, cik, company, wanted_form, matches[0])

    history = filings.get("files", [])
    if not isinstance(history, list):
        raise MalformedMetadataError("'filings.files' must be a list")

    for entry in history:
        if not isinstance(entry, dict):
            raise MalformedMetadataError("Invalid historical submissions entry")
        name = entry.get("name")
        if not isinstance(name, str) or not HISTORY_FILE_RE.match(name):
            raise MalformedMetadataError(
                "Historical submissions entry has an invalid 'name'"
            )
        block = client.get_submissions_file(name)
        matches = _select_rows(
            _rows_from_block(block), wanted_form, report_year, filing_year
        )
        if matches:
            return _build_ref(ticker, cik, company, wanted_form, matches[0])

    years = []
    if report_year is not None:
        years.append(f"report_year={report_year}")
    if filing_year is not None:
        years.append(f"filing_year={filing_year}")
    suffix = f" ({', '.join(years)})" if years else ""
    raise FilingNotFoundError(
        f"No {wanted_form} filing found for {normalize_ticker(ticker)}{suffix}"
    )
