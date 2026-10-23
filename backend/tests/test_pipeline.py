from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import main
from document_extractor import EmptyExtractionError
from filing_resolver import FilingNotFoundError, MalformedMetadataError, TickerNotFoundError
from sec_client import SecHTTPError, SecTimeoutError
from sec_pipeline import FilingResult, fetch_filing
from tests.fixtures import HTML_DOC, MSFT_SUBMISSIONS, FakeClient


def test_aapl_2025_10k_pipeline_writes_dump(tmp_path):
    result = fetch_filing(
        "AAPL",
        "10-K",
        report_year=2025,
        client=FakeClient(),
        dump_dir=tmp_path,
    )

    assert result.ticker == "AAPL"
    assert result.cik == 320193
    assert result.form_type == "10-K"
    assert result.report_date == "2025-09-27"
    assert result.filing_date == "2025-10-31"
    assert result.primary_document == "aapl-20250927.htm"
    assert result.source_url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000079/aapl-20250927.htm"
    )
    assert "Apple Inc. Annual Report" in result.extracted_text

    dump_path = Path(result.dump_path)
    assert dump_path.exists()
    assert dump_path.parent == tmp_path
    assert dump_path.read_bytes() == HTML_DOC.encode("utf-8")
    assert "AAPL_10-K_0000320193-25-000079" in dump_path.name


def test_msft_2025_10k_pipeline_writes_dump(tmp_path):
    result = fetch_filing(
        "MSFT",
        "10-K",
        report_year=2025,
        client=FakeClient(submissions=MSFT_SUBMISSIONS),
        dump_dir=tmp_path,
    )

    assert result.ticker == "MSFT"
    assert result.cik == 789019
    assert result.source_url == (
        "https://www.sec.gov/Archives/edgar/data/789019/"
        "000095017025100235/msft-20250630.htm"
    )
    dump_path = Path(result.dump_path)
    assert dump_path.exists()
    assert "MSFT_10-K_0000950170-25-100235" in dump_path.name


def test_pipeline_raises_when_no_filing_matches(tmp_path):
    with pytest.raises(FilingNotFoundError):
        fetch_filing(
            "AAPL",
            "10-K",
            report_year=1999,
            client=FakeClient(),
            dump_dir=tmp_path,
        )
    assert list(tmp_path.iterdir()) == []


def _fake_result():
    return FilingResult(
        ticker="AAPL",
        cik=320193,
        company="Apple Inc.",
        form_type="10-K",
        accession_number="0000320193-25-000079",
        filing_date="2025-10-31",
        report_date="2025-09-27",
        primary_document="aapl-20250927.htm",
        source_url=(
            "https://www.sec.gov/Archives/edgar/data/320193/"
            "000032019325000079/aapl-20250927.htm"
        ),
        content_type="text/html",
        title="Apple Inc. 10-K",
        extracted_text="Revenue was 100 billion dollars.",
        dump_path="dump/AAPL_10-K_0000320193-25-000079.htm",
        raw_content=b"<html></html>",
    )


def test_sec_filing_endpoint_returns_metadata_and_text(monkeypatch):
    monkeypatch.setattr(main, "fetch_filing", lambda *a, **k: _fake_result())
    client = TestClient(main.app)

    response = client.get(
        "/sec/filing",
        params={"ticker": "AAPL", "form_type": "10-K", "report_year": 2025},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ticker"] == "AAPL"
    assert body["cik"] == 320193
    assert body["source_url"].endswith("/aapl-20250927.htm")
    assert body["extracted_text"] == "Revenue was 100 billion dollars."
    assert body["raw_content_length"] == len(b"<html></html>")
    assert body["dump_path"].startswith("dump/")


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (TickerNotFoundError("unknown ticker"), 404),
        (FilingNotFoundError("no filing"), 404),
        (MalformedMetadataError("bad metadata"), 422),
        (EmptyExtractionError("empty"), 422),
        (SecHTTPError("sec down"), 502),
        (SecTimeoutError("timeout"), 504),
    ],
)
def test_sec_filing_endpoint_maps_errors(monkeypatch, error, status):
    def raise_error(*args, **kwargs):
        raise error

    monkeypatch.setattr(main, "fetch_filing", raise_error)
    client = TestClient(main.app)
    response = client.get("/sec/filing", params={"ticker": "AAPL"})
    assert response.status_code == status
