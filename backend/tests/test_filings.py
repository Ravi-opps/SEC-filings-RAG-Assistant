from fastapi.testclient import TestClient

import main


class FakeChunkStore:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_filings_requires_authentication():
    client = TestClient(main.app)

    response = client.get("/filings")

    assert response.status_code == 401


def test_filings_returns_catalog_and_closes_store(
    monkeypatch, auth_headers
):
    captured = {}
    chunk_store = FakeChunkStore()

    def fake_catalog(store, **kwargs):
        captured["store"] = store
        captured.update(kwargs)
        return [
            {
                "document_id": "nflx_2025_10k",
                "ticker": "NFLX",
                "company": "NETFLIX INC",
                "form_type": "10-K",
                "report_date": "2025-12-31",
                "filing_date": "2026-01-28",
                "accession_number": "0001065280-26-000034",
                "cik": "1065280",
                "title": "Netflix 2025 Annual Report",
                "source_url": "https://www.sec.gov/Archives/nflx.htm",
                "chunk_count": 1434,
                "status": "embedded",
                "indexed_at": "2026-10-30T10:00:00+00:00",
            }
        ]

    monkeypatch.setattr(main, "reload_env", lambda: {})
    monkeypatch.setattr(main, "ChunkStore", lambda: chunk_store)
    monkeypatch.setattr(main, "filing_catalog", fake_catalog)

    response = TestClient(main.app).get(
        "/filings",
        params={"ticker": "NFLX", "form_type": "10-K", "report_year": 2025},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["checked_at"]
    assert payload["filings"] == [
        {
            "document_id": "nflx_2025_10k",
            "ticker": "NFLX",
            "company": "NETFLIX INC",
            "form_type": "10-K",
            "report_date": "2025-12-31",
            "filing_date": "2026-01-28",
            "accession_number": "0001065280-26-000034",
            "cik": "1065280",
            "title": "Netflix 2025 Annual Report",
            "source_url": "https://www.sec.gov/Archives/nflx.htm",
            "chunk_count": 1434,
            "status": "embedded",
            "indexed_at": "2026-10-30T10:00:00+00:00",
        }
    ]
    assert captured["store"] is chunk_store
    assert captured["ticker"] == "NFLX"
    assert captured["year"] == 2025
    assert captured["form"] == "10-K"
    assert chunk_store.closed is True


def test_filings_returns_503_when_redis_fails(monkeypatch, auth_headers):
    chunk_store = FakeChunkStore()

    def failing_catalog(store, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(main, "reload_env", lambda: {})
    monkeypatch.setattr(main, "ChunkStore", lambda: chunk_store)
    monkeypatch.setattr(main, "filing_catalog", failing_catalog)

    response = TestClient(main.app).get("/filings")

    assert response.status_code == 503
    assert response.json()["detail"] == "Could not read filings from Redis."
    assert chunk_store.closed is True
