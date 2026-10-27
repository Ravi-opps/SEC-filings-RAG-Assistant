import json

from fastapi.testclient import TestClient

import main
from cache import RedisCache, ticker_cache_key
from filing_resolver import resolve_company
from job_queue import QueueUnavailableError
from tests.fixtures import FakeClient


class FakeRedis:
    def __init__(self):
        self.data: dict[str, str] = {}
        self.ttls: list[int | None] = []
        self.closed = False

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, ex=None):
        self.data[key] = value
        self.ttls.append(ex)
        return True

    def close(self):
        self.closed = True


class FakeCache:
    def __init__(self, data=None):
        self.data = data or {}
        self.writes = []

    def get_json(self, key):
        return self.data.get(key)

    def set_json(self, key, value):
        self.data[key] = value
        self.writes.append((key, value))
        return True


class DummyAsyncClient:
    closed = False

    async def aclose(self):
        DummyAsyncClient.closed = True


def test_cache_key_normalizes_ticker():
    assert ticker_cache_key(" aapl ") == "sec:ticker:v1:AAPL"


def test_redis_cache_round_trips_json_with_ttl():
    fake = FakeRedis()
    cache = RedisCache(client=fake, ttl=120)
    assert cache.set_json("key", {"cik": 320193}) is True
    assert json.loads(fake.data["key"]) == {"cik": 320193}
    assert fake.ttls == [120]
    assert cache.get_json("key") == {"cik": 320193}


def test_redis_cache_ignores_malformed_values():
    fake = FakeRedis()
    fake.data["bad"] = "not json"
    cache = RedisCache(client=fake)
    assert cache.get_json("bad") is None


def test_redis_cache_is_fail_open():
    class BrokenRedis:
        def get(self, key):
            raise ConnectionError("down")

        def set(self, key, value, ex=None):
            raise ConnectionError("down")

    cache = RedisCache(client=BrokenRedis())
    assert cache.get_json("key") is None
    assert cache.set_json("key", {"a": 1}) is False


def test_resolve_company_uses_cached_resolution():
    cache = FakeCache(
        {
            ticker_cache_key("AAPL"): {
                "cik": 320193,
                "company": "Apple Inc.",
                "year": 2025,
            }
        }
    )

    class ExplodingClient:
        def get_ticker_map(self):
            raise AssertionError("ticker map must not be fetched on a cache hit")

    cik, company = resolve_company("aapl", ExplodingClient(), cache=cache)
    assert (cik, company) == (320193, "Apple Inc.")


def test_resolve_company_writes_resolution_on_miss():
    cache = FakeCache()
    client = FakeClient()
    cik, company = resolve_company("AAPL", client, cache=cache, year=2025)
    assert cik == 320193
    assert company == "Apple Inc."
    assert cache.data[ticker_cache_key("AAPL")] == {
        "cik": 320193,
        "company": "Apple Inc.",
        "year": 2025,
    }


def test_ingest_cache_hit_returns_resolution_without_queueing(monkeypatch, auth_headers):
    cache = FakeCache(
        {
            ticker_cache_key("AAPL"): {
                "cik": 320193,
                "company": "Apple Inc.",
                "year": 2025,
            }
        }
    )

    async def fail_publish(job):
        raise AssertionError("a cache hit must not publish a job")

    monkeypatch.setattr(main, "get_cache", lambda: cache)
    monkeypatch.setattr(main, "publish_filing_job", fail_publish)

    client = TestClient(main.app)
    response = client.post(
        "/ingest/AAPL",
        params={"form_type": "10-K", "report_year": 2025},
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "ticker": "AAPL",
        "cik": 320193,
        "company": "Apple Inc.",
        "year": 2025,
        "form_type": "10-K",
        "cached": True,
        "status": "cached",
    }
    assert cache.writes == []


def test_ingest_cache_miss_resolves_queues_and_caches(monkeypatch, auth_headers):
    cache = FakeCache()
    published = []

    async def fake_resolve(ticker, client, cache=None, year=None):
        assert ticker == "AAPL"
        assert cache is None
        assert year == 2025
        return 320193, "Apple Inc.", year

    async def fake_publish(job):
        published.append(job)

    monkeypatch.setattr(main, "get_cache", lambda: cache)
    monkeypatch.setattr(main, "AsyncSecClient", DummyAsyncClient)
    monkeypatch.setattr(main, "resolve_company_async", fake_resolve)
    monkeypatch.setattr(main, "publish_filing_job", fake_publish)

    client = TestClient(main.app)
    response = client.post(
        "/ingest/AAPL",
        params={"form_type": "10-k", "report_year": 2025},
        headers=auth_headers,
    )

    assert response.status_code == 202
    body = response.json()
    assert body["cached"] is False
    assert body["status"] == "queued"
    assert body["cik"] == 320193
    assert body["year"] == 2025
    assert body["form_type"] == "10-K"
    assert DummyAsyncClient.closed is True

    assert len(published) == 1
    job = published[0]
    assert job["ticker"] == "AAPL"
    assert job["cik"] == 320193
    assert job["company"] == "Apple Inc."
    assert job["form_type"] == "10-K"
    assert job["report_year"] == 2025
    assert job["requested_at"]

    assert cache.data[ticker_cache_key("AAPL")] == {
        "cik": 320193,
        "company": "Apple Inc.",
        "year": 2025,
    }


def test_ingest_queue_failure_returns_503_and_does_not_cache(monkeypatch, auth_headers):
    cache = FakeCache()

    async def fake_resolve(ticker, client, cache=None, year=None):
        return 320193, "Apple Inc.", year

    async def failing_publish(job):
        raise QueueUnavailableError("kafka down")

    monkeypatch.setattr(main, "get_cache", lambda: cache)
    monkeypatch.setattr(main, "AsyncSecClient", DummyAsyncClient)
    monkeypatch.setattr(main, "resolve_company_async", fake_resolve)
    monkeypatch.setattr(main, "publish_filing_job", failing_publish)

    client = TestClient(main.app)
    response = client.post(
        "/ingest/AAPL",
        params={"report_year": 2025},
        headers=auth_headers,
    )

    assert response.status_code == 503
    assert "kafka down" in response.json()["detail"]
    assert cache.writes == []
