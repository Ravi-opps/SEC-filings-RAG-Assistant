import pytest

from filing_resolver import (
    FilingNotFoundError,
    MalformedMetadataError,
    TickerNotFoundError,
    build_archive_url,
    find_filing,
    normalize_form,
    normalize_ticker,
    resolve_company,
)
from sec_client import submissions_url
from sec_pipeline import fetch_filing
from tests.fixtures import (
    AAPL_SUBMISSIONS,
    CROSS_YEAR_SUBMISSIONS,
    HISTORICAL_FILE,
    HISTORICAL_FILE_NAME,
    HISTORICAL_SUBMISSIONS,
    FakeClient,
)


def _submissions(recent):
    return {"filings": {"recent": recent, "files": []}}


def test_ticker_normalization_is_case_and_space_insensitive():
    cik, company = resolve_company(" aapl ", FakeClient())
    assert cik == 320193
    assert "Apple" in company
    assert normalize_ticker(" msft ") == "MSFT"


def test_unknown_ticker_raises():
    with pytest.raises(TickerNotFoundError):
        resolve_company("NOPE", FakeClient())


def test_cik_formatting_uses_ten_digits_for_submissions_and_plain_for_archive():
    assert (
        submissions_url(320193)
        == "https://data.sec.gov/submissions/CIK0000320193.json"
    )
    assert submissions_url(12345).endswith("CIK0000012345.json")
    archive = build_archive_url(
        "000012345", "0000320193-25-000079", "doc.htm"
    )
    assert "/12345/" in archive
    assert "/000012345/" not in archive


def test_accession_hyphens_removed_in_archive_path():
    url = build_archive_url(
        320193, "0000320193-25-000079", "aapl-20250927.htm"
    )
    assert "000032019325000079" in url
    assert "0000320193-25-000079" not in url


def test_url_uses_exact_primary_document():
    url = build_archive_url(
        320193, "0000320193-25-000079", "unusual-filing-name_2025.htm"
    )
    assert url.endswith("/unusual-filing-name_2025.htm")


def test_url_correctness_matches_expected_fixture_url():
    url = build_archive_url(
        320193, "0000320193-25-000079", "aapl-20250927.htm"
    )
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019325000079/aapl-20250927.htm"
    )


def test_url_quotes_path_segments():
    url = build_archive_url(
        320193, "0000320193-25-000079", "folder/a file.htm"
    )
    assert url.endswith("/folder/a%20file.htm")


@pytest.mark.parametrize(
    "document",
    ["", "../escape.htm", "https://evil.test/file.htm", "\\windows\\file.htm"],
)
def test_url_rejects_untrusted_document_paths(document):
    with pytest.raises(MalformedMetadataError):
        build_archive_url(320193, "0000320193-25-000079", document)


def test_form_filtering_never_returns_another_form():
    client = FakeClient()
    ten_k = find_filing(client, "AAPL", "10-K", report_year=2025)
    ten_q = find_filing(client, "AAPL", "10-q", report_year=2025)

    assert ten_k.form_type == "10-K"
    assert ten_k.accession_number == "0000320193-25-000079"
    assert ten_q.form_type == "10-Q"
    assert ten_q.accession_number == "0000320193-25-000050"
    assert normalize_form(" 10-k ") == "10-K"


def test_report_year_and_filing_year_are_distinct():
    client = FakeClient(submissions=CROSS_YEAR_SUBMISSIONS)

    report_match = find_filing(client, "AAPL", "10-K", report_year=2024)
    assert report_match.report_date == "2024-12-28"
    assert report_match.filing_date == "2025-01-15"

    filing_match = find_filing(client, "AAPL", "10-K", filing_year=2025)
    assert filing_match.report_date == "2024-12-28"
    assert filing_match.filing_date == "2025-01-15"

    next_report = find_filing(client, "AAPL", "10-K", report_year=2025)
    assert next_report.report_date == "2025-12-27"
    assert next_report.filing_date == "2026-01-14"
    assert next_report.accession_number == "0000320193-26-000001"


def test_no_matching_filing_raises_clear_error():
    with pytest.raises(FilingNotFoundError):
        find_filing(FakeClient(), "AAPL", "8-K", report_year=2025)


def test_no_matching_filing_does_not_download_anything(tmp_path):
    client = FakeClient()
    with pytest.raises(FilingNotFoundError):
        fetch_filing(
            "AAPL",
            "8-K",
            report_year=2025,
            client=client,
            dump_dir=tmp_path,
        )
    assert client.document_calls == []


def test_missing_required_array_fails_clearly():
    bad = _submissions(
        {
            "accessionNumber": ["0000320193-25-000079"],
            "filingDate": ["2025-10-31"],
            "reportDate": ["2025-09-27"],
            "form": ["10-K"],
        }
    )
    with pytest.raises(MalformedMetadataError):
        find_filing(FakeClient(submissions=bad), "AAPL", "10-K")


def test_inconsistent_array_lengths_fail_clearly():
    bad = _submissions(
        {
            "accessionNumber": [
                "0000320193-25-000079",
                "0000320193-25-000080",
            ],
            "filingDate": ["2025-10-31"],
            "reportDate": ["2025-09-27"],
            "form": ["10-K"],
            "primaryDocument": ["aapl-20250927.htm"],
        }
    )
    with pytest.raises(MalformedMetadataError):
        find_filing(FakeClient(submissions=bad), "AAPL", "10-K")


def test_missing_accession_number_fails_clearly():
    bad = _submissions(
        {
            "accessionNumber": [""],
            "filingDate": ["2025-10-31"],
            "reportDate": ["2025-09-27"],
            "form": ["10-K"],
            "primaryDocument": ["aapl-20250927.htm"],
        }
    )
    with pytest.raises(MalformedMetadataError):
        find_filing(FakeClient(submissions=bad), "AAPL", "10-K")


def test_missing_primary_document_fails_clearly():
    bad = _submissions(
        {
            "accessionNumber": ["0000320193-25-000079"],
            "filingDate": ["2025-10-31"],
            "reportDate": ["2025-09-27"],
            "form": ["10-K"],
            "primaryDocument": [""],
        }
    )
    with pytest.raises(MalformedMetadataError):
        find_filing(FakeClient(submissions=bad), "AAPL", "10-K")


def test_historical_submissions_file_is_searched():
    client = FakeClient(
        submissions=HISTORICAL_SUBMISSIONS,
        history={HISTORICAL_FILE_NAME: HISTORICAL_FILE},
    )
    ref = find_filing(client, "AAPL", "10-K", report_year=2015)
    assert ref.accession_number == "0000320193-15-000010"
    assert ref.primary_document == "aapl-20150926.htm"


def test_unsafe_historical_file_name_is_rejected():
    bad = _submissions(
        {
            "accessionNumber": [],
            "filingDate": [],
            "reportDate": [],
            "form": [],
            "primaryDocument": [],
        }
    )
    bad["filings"]["files"] = [{"name": "../secret.json"}]
    with pytest.raises(MalformedMetadataError):
        find_filing(FakeClient(submissions=bad), "AAPL", "10-K", report_year=2015)


def test_default_selection_is_latest_filing_date():
    ref = find_filing(FakeClient(submissions=AAPL_SUBMISSIONS), "AAPL", "10-K")
    assert ref.filing_date == "2025-10-31"
