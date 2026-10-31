import fnmatch
import json

from fastapi.testclient import TestClient

import main
from qdrant_load_status import document_reports


class FakeRedis:
    def __init__(self, values=None):
        self.values = values or {}

    def scan_iter(self, match=None, count=200):
        return iter(
            key
            for key in self.values
            if match is None or fnmatch.fnmatch(key, match)
        )

    def get(self, key):
        return self.values.get(key)

    def zcard(self, key):
        return 0


class FakeChunkStore:
    def __init__(self, metas):
        self.metas = metas
        values = {
            f"sec:doc:v1:{document_id}:meta": json.dumps(meta)
            for document_id, meta in metas.items()
        }
        self.client = FakeRedis(values)
        self.closed = False

    def get_meta(self, document_id):
        return self.metas.get(document_id)

    def get_indexed(self, key):
        return None

    def close(self):
        self.closed = True


class FakeQdrantStore:
    def __init__(self, counts):
        self.counts = counts
        self.closed = False

    def collection_exists(self):
        return True

    def count(self, *, document_id=None):
        return self.counts.get(document_id, 0)

    def close(self):
        self.closed = True


def _metas():
    return {
        "nflx_2025_10k": {
            "document_id": "nflx_2025_10k",
            "ticker": "NFLX",
            "company": "NETFLIX INC",
            "form_type": "10-K",
            "report_date": "2025-12-31",
            "accession_number": "0001065280-26-000034",
            "chunk_count": 1434,
        },
        "aapl_2025_10k": {
            "document_id": "aapl_2025_10k",
            "ticker": "AAPL",
            "company": "Apple Inc.",
            "form_type": "10-K",
            "report_date": "2025-09-27",
            "accession_number": "0000320193-25-000079",
            "chunk_count": 1242,
        },
    }


def test_document_reports_covers_all_documents_latest_first():
    chunk_store = FakeChunkStore(_metas())
    store = FakeQdrantStore({"nflx_2025_10k": 1434, "aapl_2025_10k": 4})

    reports = document_reports(chunk_store, store)

    assert [report["document_id"] for report in reports] == [
        "nflx_2025_10k",
        "aapl_2025_10k",
    ]
    assert reports[0]["state"] == "complete"
    assert reports[0]["loaded_chunks"] == 1434
    assert reports[1]["state"] == "in_progress"
    assert reports[1]["remaining_chunks"] == 1238


def test_document_reports_can_limit_to_latest_match():
    chunk_store = FakeChunkStore(_metas())
    store = FakeQdrantStore({"nflx_2025_10k": 1434})

    reports = document_reports(
        chunk_store,
        store,
        ticker="NFLX",
        all_documents=False,
    )

    assert [report["document_id"] for report in reports] == ["nflx_2025_10k"]


def test_ingest_status_requires_authentication():
    client = TestClient(main.app)

    response = client.get("/ingest/status")

    assert response.status_code == 401


def test_ingest_status_returns_reports_and_closes_stores(
    monkeypatch, auth_headers
):
    captured = {}
    chunk_store = FakeChunkStore({})
    qdrant_store = FakeQdrantStore({})

    def fake_reports(chunk_store_arg, store_arg, **kwargs):
        captured["chunk_store"] = chunk_store_arg
        captured["store"] = store_arg
        captured.update(kwargs)
        return [
            {
                "document_id": "nflx_2025_10k",
                "ticker": "NFLX",
                "loaded_chunks": 1434,
                "state": "complete",
            }
        ]

    monkeypatch.setattr(main, "reload_env", lambda: {"QDRANT_URL": "http://qdrant"})
    monkeypatch.setattr(main, "ChunkStore", lambda: chunk_store)
    monkeypatch.setattr(main, "QdrantStore", lambda: qdrant_store)
    monkeypatch.setattr(main, "document_reports", fake_reports)

    response = TestClient(main.app).get(
        "/ingest/status",
        params={"ticker": "NFLX", "report_year": 2025},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["documents"] == [
        {
            "document_id": "nflx_2025_10k",
            "ticker": "NFLX",
            "loaded_chunks": 1434,
            "state": "complete",
        }
    ]
    assert payload["checked_at"]
    assert captured["chunk_store"] is chunk_store
    assert captured["store"] is qdrant_store
    assert captured["ticker"] == "NFLX"
    assert captured["year"] == 2025
    assert captured["form"] is None
    assert captured["document_id"] is None
    assert captured["all_documents"] is True
    assert chunk_store.closed is True
    assert qdrant_store.closed is True


def test_ingest_status_returns_503_without_qdrant(
    monkeypatch, auth_headers
):
    monkeypatch.setattr(main, "reload_env", lambda: {})

    response = TestClient(main.app).get("/ingest/status")

    assert response.status_code == 503
    assert response.json()["detail"] == "QDRANT_URL is not configured."
