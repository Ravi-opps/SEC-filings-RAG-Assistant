"""Async SEC EDGAR client for the API and the loader worker."""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Callable
from typing import Any

import httpx

from cache import ticker_cache_key
from filing_resolver import (
    HISTORY_FILE_RE,
    FilingNotFoundError,
    FilingRef,
    MalformedMetadataError,
    TickerNotFoundError,
    _build_ref,
    _rows_from_block,
    _select_rows,
    company_from_mapping,
    normalize_form,
    normalize_ticker,
    parse_cached_company,
)
from sec_client import (
    DEFAULT_TIMEOUT,
    DEFAULT_USER_AGENT,
    SUBMISSIONS_BASE,
    TICKER_MAP_URL,
    SecConnectionError,
    SecHTTPError,
    SecInvalidJSONError,
    SecTimeoutError,
    submissions_url,
)


def _content_length(headers: httpx.Headers) -> int | None:
    raw = headers.get("Content-Length")
    if not raw:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _decode_body(raw: bytes, encoding: str) -> bytes:
    encoding = (encoding or "").split(",")[0].strip().lower()
    if encoding in {"", "identity"}:
        return raw
    try:
        if encoding == "gzip":
            return gzip.decompress(raw)
        if encoding == "deflate":
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return zlib.decompress(raw, -zlib.MAX_WBITS)
    except (OSError, EOFError, zlib.error) as exc:
        raise SecHTTPError(f"Could not decode {encoding} response body") from exc
    raise SecHTTPError(f"Unsupported Content-Encoding: {encoding}")


class AsyncSecClient:
    """httpx-based client with the same endpoints as ``SecClient``."""

    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.timeout = timeout
        self._client = httpx.AsyncClient(
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=timeout,
            follow_redirects=True,
            transport=transport,
        )
        self._ticker_map: dict | None = None

    async def _get(self, url: str) -> httpx.Response:
        try:
            response = await self._client.get(url)
        except httpx.TimeoutException as exc:
            raise SecTimeoutError(
                f"Timed out after {self.timeout}s requesting {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise SecConnectionError(f"Connection failed requesting {url}") from exc

        if response.status_code >= 400:
            raise SecHTTPError(
                f"SEC returned HTTP {response.status_code} for {url}"
            )
        return response

    async def get_json(self, url: str) -> dict:
        response = await self._get(url)
        try:
            data = response.json()
        except ValueError as exc:
            raise SecInvalidJSONError(f"Invalid JSON from {url}") from exc
        if not isinstance(data, dict):
            raise SecInvalidJSONError(f"Expected a JSON object from {url}")
        return data

    async def _probe_length(self, url: str) -> tuple[int | None, str]:
        """Best-effort wire size and encoding from a HEAD request.

        SEC archive responses are chunked, so GET has no Content-Length. The
        HEAD response usually reports the compressed size, which is exactly
        what the progress bar counts while streaming raw bytes.
        """
        try:
            response = await self._client.head(url)
        except httpx.HTTPError:
            return None, ""
        if response.status_code >= 400:
            return None, ""
        return (
            _content_length(response.headers),
            response.headers.get("Content-Encoding", "").lower(),
        )

    async def get_document(
        self,
        url: str,
        on_progress: Callable[[int, int | None], None] | None = None,
    ) -> tuple[bytes, str]:
        if on_progress is None:
            response = await self._get(url)
            content_type = response.headers.get("Content-Type", "")
            return response.content, content_type

        probe_total, probe_encoding = await self._probe_length(url)
        try:
            async with self._client.stream("GET", url) as response:
                if response.status_code >= 400:
                    raise SecHTTPError(
                        f"SEC returned HTTP {response.status_code} for {url}"
                    )
                content_type = response.headers.get("Content-Type", "")
                encoding = response.headers.get("Content-Encoding", "").lower()
                total = _content_length(response.headers)
                if total is None and probe_encoding == encoding:
                    total = probe_total

                raw_chunks: list[bytes] = []
                received = 0
                on_progress(0, total)
                async for chunk in response.aiter_raw():
                    raw_chunks.append(chunk)
                    received += len(chunk)
                    on_progress(received, total)
                body = _decode_body(b"".join(raw_chunks), encoding)
                return body, content_type
        except httpx.TimeoutException as exc:
            raise SecTimeoutError(
                f"Timed out after {self.timeout}s requesting {url}"
            ) from exc
        except httpx.RequestError as exc:
            raise SecConnectionError(f"Connection failed requesting {url}") from exc

    async def get_ticker_map(self) -> dict:
        if self._ticker_map is None:
            self._ticker_map = await self.get_json(TICKER_MAP_URL)
        return self._ticker_map

    async def get_submissions(self, cik: int | str) -> dict:
        return await self.get_json(submissions_url(cik))

    async def get_submissions_file(self, name: str) -> dict:
        return await self.get_json(f"{SUBMISSIONS_BASE}/{name}")

    async def aclose(self) -> None:
        await self._client.aclose()


async def resolve_company_async(
    ticker: str,
    client: AsyncSecClient,
    cache: Any | None = None,
    year: int | None = None,
) -> tuple[int, str, int | None]:
    """Resolve a ticker asynchronously, caching (company, CIK, year)."""
    wanted = normalize_ticker(ticker)
    if not wanted:
        raise TickerNotFoundError("Ticker must not be empty")

    if cache is not None:
        cached = parse_cached_company(cache.get_json(ticker_cache_key(wanted)))
        if cached is not None:
            return cached

    cik, company = company_from_mapping(await client.get_ticker_map(), wanted)
    if cache is not None:
        cache.set_json(
            ticker_cache_key(wanted),
            {"cik": cik, "company": company, "year": year},
        )
    return cik, company, year


async def find_filing_async(
    client: AsyncSecClient,
    ticker: str,
    form_type: str,
    report_year: int | None = None,
    filing_year: int | None = None,
    *,
    cik: int | None = None,
    company: str | None = None,
    cache: Any | None = None,
) -> FilingRef:
    """Resolve the newest filing matching the form and year criteria."""
    wanted = normalize_ticker(ticker)
    if cik is None:
        cik, company, _ = await resolve_company_async(
            wanted,
            client,
            cache=cache,
            year=report_year if report_year is not None else filing_year,
        )
    company = company or ""
    wanted_form = normalize_form(form_type)
    submissions = await client.get_submissions(cik)

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
        return _build_ref(wanted, cik, company, wanted_form, matches[0])

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
        block = await client.get_submissions_file(name)
        matches = _select_rows(
            _rows_from_block(block), wanted_form, report_year, filing_year
        )
        if matches:
            return _build_ref(wanted, cik, company, wanted_form, matches[0])

    years = []
    if report_year is not None:
        years.append(f"report_year={report_year}")
    if filing_year is not None:
        years.append(f"filing_year={filing_year}")
    suffix = f" ({', '.join(years)})" if years else ""
    raise FilingNotFoundError(
        f"No {wanted_form} filing found for {wanted}{suffix}"
    )
