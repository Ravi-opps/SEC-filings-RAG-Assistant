"""HTTP client for SEC EDGAR public endpoints."""

from __future__ import annotations

import requests

TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

DEFAULT_USER_AGENT = "RAG assistant ravikrishnap347@gmail.com"
DEFAULT_TIMEOUT = 30.0


class SecError(Exception):
    """Base class for SEC client failures."""


class SecHTTPError(SecError):
    """SEC returned a non-success HTTP status."""


class SecTimeoutError(SecError):
    """The request to SEC timed out."""


class SecConnectionError(SecError):
    """The connection to SEC failed."""


class SecInvalidJSONError(SecError):
    """SEC returned a response that was not a valid JSON object."""


def submissions_url(cik: int | str) -> str:
    return f"{SUBMISSIONS_BASE}/CIK{str(cik).zfill(10)}.json"


class SecClient:
    def __init__(
        self,
        user_agent: str = DEFAULT_USER_AGENT,
        timeout: float = DEFAULT_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
            }
        )
        self._ticker_map: dict | None = None

    def _get(self, url: str) -> requests.Response:
        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.Timeout as exc:
            raise SecTimeoutError(
                f"Timed out after {self.timeout}s requesting {url}"
            ) from exc
        except requests.ConnectionError as exc:
            raise SecConnectionError(f"Connection failed requesting {url}") from exc
        except requests.RequestException as exc:
            raise SecError(f"Request failed for {url}: {exc}") from exc

        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            raise SecHTTPError(
                f"SEC returned HTTP {response.status_code} for {url}"
            ) from exc
        return response

    def get_json(self, url: str) -> dict:
        response = self._get(url)
        try:
            data = response.json()
        except ValueError as exc:
            raise SecInvalidJSONError(f"Invalid JSON from {url}") from exc
        if not isinstance(data, dict):
            raise SecInvalidJSONError(f"Expected a JSON object from {url}")
        return data

    def get_document(self, url: str) -> tuple[bytes, str]:
        response = self._get(url)
        content_type = response.headers.get("Content-Type", "")
        return response.content, content_type

    def get_ticker_map(self) -> dict:
        if self._ticker_map is None:
            self._ticker_map = self.get_json(TICKER_MAP_URL)
        return self._ticker_map

    def get_submissions(self, cik: int | str) -> dict:
        return self.get_json(submissions_url(cik))

    def get_submissions_file(self, name: str) -> dict:
        return self.get_json(f"{SUBMISSIONS_BASE}/{name}")

    def clear_cache(self) -> None:
        self._ticker_map = None
