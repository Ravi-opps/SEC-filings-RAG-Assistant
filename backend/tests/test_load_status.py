import json

from qdrant_load_status import (
    build_report,
    filing_catalog,
    load_documents,
    render_compact,
    render_report,
)


class FakeRedis:
    def __init__(self, values):
        self.values = values

    def scan_iter(self, match=None, count=200):
        import fnmatch

        return iter(
            key
            for key in self.values
            if match is None or fnmatch.fnmatch(key, match)
        )

    def get(self, key):
        return self.values.get(key)


class FakeChunkStore:
    def __init__(self, metas=None, indexed=None, statuses=None):
        self.metas = metas or {}
        self.indexed = indexed or {}
        self.statuses = statuses or {}
        values = {}
        for document_id, meta in self.metas.items():
            values[f"sec:doc:v1:{document_id}:meta"] = json.dumps(meta)
        for key, payload in self.indexed.items():
            values[f"sec:indexed:v1:{key}"] = json.dumps(payload)
        for document_id, status in self.statuses.items():
            values[f"sec:doc:v1:{document_id}:status"] = json.dumps(status)
        self.client = FakeRedis(values)

    def get_meta(self, document_id):
        return self.metas.get(document_id)

    def get_indexed(self, key):
        prefix = key.rsplit(":", 1)[-1]
        return self.indexed.get(prefix)


def _report(**overrides):
    values = {
        "document_id": "nflx_2025_10k",
        "meta": {
            "ticker": "NFLX",
            "company": "NETFLIX INC",
            "form_type": "10-K",
            "report_date": "2025-12-31",
            "accession_number": "0001065280-26-000034",
            "chunk_count": 1434,
        },
        "redis_status": {"status": "chunked", "chunk_count": 1434},
        "indexed": {},
        "pending_chunks": 1370,
        "loaded_chunks": 64,
        "count_source": "payload filter",
    }
    values.update(overrides)
    return build_report(**values)


def test_in_progress_report_uses_expected_and_loaded_counts():
    report = _report()

    assert report.expected_chunks == 1434
    assert report.loaded_chunks == 64
    assert report.remaining_chunks == 1370
    assert report.percent_loaded == 4.5
    assert report.percent_remaining == 95.5
    assert report.complete is False
    assert report.state == "in_progress"
    assert "1370" in report.verdict
    assert "still to load" in report.verdict


def test_complete_report_reads_indexed_metadata():
    report = _report(
        meta={},
        redis_status={"status": "embedded"},
        indexed={
            "document_id": "nflx_2025_10k",
            "ticker": "NFLX",
            "points_upserted": 1434,
            "indexed_at": "2026-10-30T10:00:00+00:00",
        },
        pending_chunks=0,
        loaded_chunks=1434,
    )

    assert report.expected_chunks == 1434
    assert report.remaining_chunks == 0
    assert report.percent_loaded == 100.0
    assert report.complete is True
    assert report.state == "complete"
    assert report.ticker == "NFLX"
    assert report.indexed_at == "2026-10-30T10:00:00+00:00"


def test_unknown_expected_count_still_reports_qdrant_points():
    report = _report(meta={}, redis_status={}, loaded_chunks=12)

    assert report.expected_chunks is None
    assert report.remaining_chunks is None
    assert report.complete is None
    assert report.state == "unknown"
    assert "12" in report.verdict


def test_more_points_than_expected_is_complete_with_note():
    report = _report(loaded_chunks=1500, pending_chunks=0)

    assert report.complete is True
    assert report.state == "complete"
    assert any("more points" in note for note in report.notes)


def test_full_scan_report_hints_at_index_creation():
    report = _report(
        count_source="full collection scan (document_id index missing)"
    )

    assert any("--create-index" in note for note in report.notes)


def test_renderers_include_loaded_remaining_and_pending():
    report = _report()

    full = render_report(report, timestamp="2026-10-30 12:00:00")
    compact = render_compact(report, timestamp="2026-10-30 12:00:00")

    assert "expected        : 1434 chunks" in full
    assert "loaded          : 64 chunks (4.5%)" in full
    assert "remaining       : 1370 chunks (95.5%)" in full
    assert "redis pending   : 1370 chunks" in full
    assert "4.5%" in compact
    assert "remaining=1370" in compact
    assert "state=in_progress" in compact


def test_load_documents_filters_by_ticker_year_and_form():
    store = FakeChunkStore(
        metas={
            "nflx_2025_10k": {
                "document_id": "nflx_2025_10k",
                "ticker": "NFLX",
                "form_type": "10-K",
                "report_date": "2025-12-31",
            },
            "nflx_2024_10k": {
                "document_id": "nflx_2024_10k",
                "ticker": "NFLX",
                "form_type": "10-K",
                "report_date": "2024-12-31",
            },
            "aapl_2025_10k": {
                "document_id": "aapl_2025_10k",
                "ticker": "AAPL",
                "form_type": "10-K",
                "report_date": "2025-09-27",
            },
        }
    )

    hints = load_documents(
        store,
        ticker="nflx",
        year=2025,
        form="10-k",
        document_id=None,
    )

    assert [hint.document_id for hint in hints] == ["nflx_2025_10k"]


def test_load_documents_uses_indexed_metadata_after_meta_expiry():
    store = FakeChunkStore(
        indexed={
            "0000320193-25-000079": {
                "document_id": "aapl_2025_10k",
                "ticker": "AAPL",
                "form_type": "10-K",
                "report_date": "2025-09-27",
                "points_upserted": 1242,
            }
        }
    )

    hints = load_documents(
        store,
        ticker="AAPL",
        year=2025,
        form="10-K",
        document_id=None,
    )

    assert [hint.document_id for hint in hints] == ["aapl_2025_10k"]
    assert hints[0].meta["chunk_count"] == 1242


def test_filing_catalog_merges_metadata_indexed_records_and_status():
    store = FakeChunkStore(
        metas={
            "nflx_2025_10k": {
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
            }
        },
        indexed={
            "0001065280-26-000034": {
                "document_id": "nflx_2025_10k",
                "indexed_at": "2026-10-30T10:00:00+00:00",
                "points_upserted": 1434,
            },
            "0000320193-25-000079": {
                "document_id": "aapl_2025_10k",
                "ticker": "AAPL",
                "company": "Apple Inc.",
                "form_type": "10-K",
                "report_date": "2025-09-27",
                "filing_date": "2025-10-31",
                "accession_number": "0000320193-25-000079",
                "cik": "320193",
                "source_url": "https://www.sec.gov/Archives/aapl.htm",
                "points_upserted": 1242,
                "indexed_at": "2026-10-29T09:00:00+00:00",
            },
        },
        statuses={"nflx_2025_10k": {"status": "embedded"}},
    )

    filings = filing_catalog(store)

    assert [filing["document_id"] for filing in filings] == [
        "nflx_2025_10k",
        "aapl_2025_10k",
    ]
    netflix = filings[0]
    assert netflix["status"] == "embedded"
    assert netflix["chunk_count"] == 1434
    assert netflix["title"] == "Netflix 2025 Annual Report"
    assert netflix["indexed_at"] == "2026-10-30T10:00:00+00:00"
    assert netflix["filing_date"] == "2026-01-28"
    apple = filings[1]
    assert apple["status"] == "indexed"
    assert apple["chunk_count"] == 1242
    assert apple["ticker"] == "AAPL"
    assert apple["source_url"] == "https://www.sec.gov/Archives/aapl.htm"


def test_filing_catalog_filters_by_ticker_year_and_form():
    store = FakeChunkStore(
        metas={
            "nflx_2025_10k": {
                "document_id": "nflx_2025_10k",
                "ticker": "NFLX",
                "form_type": "10-K",
                "report_date": "2025-12-31",
            },
            "nflx_2024_10k": {
                "document_id": "nflx_2024_10k",
                "ticker": "NFLX",
                "form_type": "10-K",
                "report_date": "2024-12-31",
            },
            "aapl_2025_10k": {
                "document_id": "aapl_2025_10k",
                "ticker": "AAPL",
                "form_type": "10-K",
                "report_date": "2025-09-27",
            },
        }
    )

    filings = filing_catalog(store, ticker="nflx", year=2025, form="10-k")

    assert [filing["document_id"] for filing in filings] == ["nflx_2025_10k"]
