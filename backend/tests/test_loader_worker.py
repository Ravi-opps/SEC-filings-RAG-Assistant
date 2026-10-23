import asyncio
import gzip

import httpx
import pytest

import loader_worker
from async_sec import AsyncSecClient, find_filing_async, resolve_company_async
from cache import ticker_cache_key
from tests.fixtures import AAPL_SUBMISSIONS, HTML_DOC, TICKER_MAP


class FakeAsyncSecClient:
    def __init__(self, *args, **kwargs):
        self.closed = False
        self.document_urls = []

    async def get_ticker_map(self):
        return TICKER_MAP

    async def get_submissions(self, cik):
        assert str(cik) == "320193"
        return AAPL_SUBMISSIONS

    async def get_submissions_file(self, name):
        raise AssertionError(f"unexpected history file {name}")

    async def get_document(self, url, on_progress=None):
        self.document_urls.append(url)
        raw = HTML_DOC.encode("utf-8")
        if on_progress is not None:
            on_progress(0, len(raw))
            on_progress(len(raw), len(raw))
        return raw, "text/html"

    async def aclose(self):
        self.closed = True


class FakeCache:
    def __init__(self):
        self.data = {}

    def get_json(self, key):
        return self.data.get(key)

    def set_json(self, key, value):
        self.data[key] = value
        return True


def test_resolve_company_async_caches_company_cik_and_year():
    cache = FakeCache()
    client = FakeAsyncSecClient()

    cik, company, year = asyncio.run(
        resolve_company_async("aapl", client, cache=cache, year=2025)
    )

    assert (cik, company, year) == (320193, "Apple Inc.", 2025)
    assert cache.data[ticker_cache_key("AAPL")] == {
        "cik": 320193,
        "company": "Apple Inc.",
        "year": 2025,
    }


def test_resolve_company_async_uses_cache_without_network():
    cache = FakeCache()
    cache.data[ticker_cache_key("AAPL")] = {
        "cik": 320193,
        "company": "Apple Inc.",
        "year": 2024,
    }

    class ExplodingClient:
        async def get_ticker_map(self):
            raise AssertionError("network must not be used on a cache hit")

    cik, company, year = asyncio.run(
        resolve_company_async("AAPL", ExplodingClient(), cache=cache, year=2025)
    )
    assert (cik, company, year) == (320193, "Apple Inc.", 2024)


def test_find_filing_async_builds_the_archive_url():
    client = FakeAsyncSecClient()
    ref = asyncio.run(
        find_filing_async(
            client,
            "AAPL",
            "10-K",
            report_year=2025,
            cik=320193,
            company="Apple Inc.",
        )
    )

    assert ref.accession_number == "0000320193-25-000079"
    assert ref.primary_document == "aapl-20250927.htm"
    assert (
        ref.source_url
        == "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
    )


def _install_fake_client(monkeypatch):
    created = []

    def factory(*args, **kwargs):
        client = FakeAsyncSecClient()
        created.append(client)
        return client

    monkeypatch.setattr(loader_worker, "AsyncSecClient", factory)
    return created


JOB = {
    "ticker": "AAPL",
    "cik": 320193,
    "company": "Apple Inc.",
    "form_type": "10-K",
    "report_year": 2025,
    "filing_year": None,
    "requested_at": "2026-10-21T00:00:00+00:00",
}


def test_loader_worker_downloads_the_html_document(monkeypatch, tmp_path):
    created = _install_fake_client(monkeypatch)

    async def fake_publish(job):
        pass

    monkeypatch.setattr(loader_worker, "publish_index_job", fake_publish)
    monkeypatch.setenv("DUMP_DIR", str(tmp_path))

    path = asyncio.run(loader_worker.process_job(dict(JOB), show_progress=False))

    assert path.exists()
    assert path.read_text(encoding="utf-8") == HTML_DOC
    assert path.name == "AAPL_10-K_0000320193-25-000079.htm"
    assert created[0].document_urls == [
        "https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm"
    ]
    assert created[0].closed is True


def test_loader_worker_publishes_a_step2_job_after_download(
    monkeypatch, tmp_path
):
    _install_fake_client(monkeypatch)
    published = []

    async def fake_publish(job):
        published.append(job)

    monkeypatch.setattr(loader_worker, "publish_index_job", fake_publish)
    monkeypatch.setenv("DUMP_DIR", str(tmp_path))

    path = asyncio.run(loader_worker.process_job(dict(JOB), show_progress=False))

    assert len(published) == 1
    job = published[0]
    assert job["stage"] == "index"
    assert job["ticker"] == "AAPL"
    assert job["cik"] == 320193
    assert job["form_type"] == "10-K"
    assert job["accession_number"] == "0000320193-25-000079"
    assert job["report_date"] == "2025-09-27"
    assert job["filing_date"] == "2025-10-31"
    assert job["primary_document"] == "aapl-20250927.htm"
    assert job["source_url"].endswith("/aapl-20250927.htm")
    assert job["saved_path"] == str(path)
    assert job["content_type"] == "text/html"
    assert job["bytes"] == len(HTML_DOC.encode("utf-8"))
    assert job["report_year"] == 2025
    assert job["filing_year"] is None
    assert job["requested_at"] == JOB["requested_at"]
    assert job["downloaded_at"]


def test_loader_worker_closes_client_when_step2_publish_fails(
    monkeypatch, tmp_path
):
    created = _install_fake_client(monkeypatch)

    async def failing_publish(job):
        raise RuntimeError("kafka unavailable")

    monkeypatch.setattr(loader_worker, "publish_index_job", failing_publish)
    monkeypatch.setenv("DUMP_DIR", str(tmp_path))

    with pytest.raises(RuntimeError, match="kafka unavailable"):
        asyncio.run(loader_worker.process_job(dict(JOB), show_progress=False))

    assert created[0].closed is True


def test_get_document_streams_with_progress_callbacks():
    body = (HTML_DOC * 2).encode("utf-8")
    seen_requests = []

    def handler(request):
        seen_requests.append(request)
        return httpx.Response(
            200,
            headers={
                "Content-Type": "text/html",
                "Content-Length": str(len(body)),
            },
            stream=httpx.ByteStream(body),
        )

    async def run():
        client = AsyncSecClient(transport=httpx.MockTransport(handler))
        calls = []
        try:
            raw, content_type = await client.get_document(
                "https://example.com/aapl.htm",
                on_progress=lambda done, total: calls.append((done, total)),
            )
            return raw, content_type, calls
        finally:
            await client.aclose()

    raw, content_type, calls = asyncio.run(run())

    assert raw == body
    assert content_type == "text/html"
    assert [request.method for request in seen_requests] == ["HEAD", "GET"]
    assert calls[0] == (0, len(body))
    assert calls[-1] == (len(body), len(body))
    assert all(total == len(body) for _, total in calls)


def test_get_document_reports_progress_for_chunked_gzip_responses():
    payload = HTML_DOC.encode("utf-8")
    compressed = gzip.compress(payload)
    methods = []

    def handler(request):
        methods.append(request.method)
        if request.method == "HEAD":
            return httpx.Response(
                200,
                headers={
                    "Content-Encoding": "gzip",
                    "Content-Length": str(len(compressed)),
                },
            )
        return httpx.Response(
            200,
            headers={"Content-Type": "text/html", "Content-Encoding": "gzip"},
            stream=httpx.ByteStream(compressed),
        )

    async def run():
        client = AsyncSecClient(transport=httpx.MockTransport(handler))
        calls = []
        try:
            raw, content_type = await client.get_document(
                "https://example.com/aapl.htm",
                on_progress=lambda done, total: calls.append((done, total)),
            )
            return raw, content_type, calls
        finally:
            await client.aclose()

    raw, content_type, calls = asyncio.run(run())

    assert methods == ["HEAD", "GET"]
    assert raw == payload
    assert content_type == "text/html"
    assert calls[0] == (0, len(compressed))
    assert calls[-1] == (len(compressed), len(compressed))
    assert all(total == len(compressed) for _, total in calls)


def test_loader_worker_wires_progress_callbacks_to_the_ui(
    monkeypatch, tmp_path
):
    _install_fake_client(monkeypatch)
    events = []

    class FakeUI:
        def __init__(self, label, *, enabled=None, **kwargs):
            self.enabled = enabled
            events.append(("init", label, enabled))

        def start(self):
            events.append(("start",))

        def status(self, text):
            events.append(("status", text))

        def update(self, downloaded, total=None):
            events.append(("update", downloaded, total))

        def finish(self, detail=""):
            events.append(("finish", detail))

        def fail(self):
            events.append(("fail",))

        def close(self):
            events.append(("close",))

    async def fake_publish(job):
        pass

    monkeypatch.setattr(loader_worker, "DownloadUI", FakeUI)
    monkeypatch.setattr(loader_worker, "publish_index_job", fake_publish)
    monkeypatch.setenv("DUMP_DIR", str(tmp_path))

    asyncio.run(loader_worker.process_job(dict(JOB), show_progress=True))

    size = len(HTML_DOC.encode("utf-8"))
    assert ("init", "AAPL 10-K", True) in events
    assert ("start",) in events
    assert ("update", 0, size) in events
    assert ("update", size, size) in events
    assert any(
        event[0] == "finish" and "bytes" in event[1] for event in events
    )
