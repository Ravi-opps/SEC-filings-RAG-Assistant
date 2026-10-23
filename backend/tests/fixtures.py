"""Deterministic offline fixtures for the SEC pipeline tests."""

TICKER_MAP = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft Corporation"},
}

AAPL_SUBMISSIONS = {
    "cik": "320193",
    "entityName": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-25-000079",
                "0000320193-25-000050",
            ],
            "filingDate": ["2025-10-31", "2025-08-01"],
            "reportDate": ["2025-09-27", "2025-06-28"],
            "form": ["10-K", "10-Q"],
            "primaryDocument": ["aapl-20250927.htm", "aapl-20250628.htm"],
        },
        "files": [],
    },
}

MSFT_SUBMISSIONS = {
    "cik": "789019",
    "entityName": "MICROSOFT CORP",
    "filings": {
        "recent": {
            "accessionNumber": ["0000950170-25-100235"],
            "filingDate": ["2025-07-30"],
            "reportDate": ["2025-06-30"],
            "form": ["10-K"],
            "primaryDocument": ["msft-20250630.htm"],
        },
        "files": [],
    },
}

CROSS_YEAR_SUBMISSIONS = {
    "cik": "320193",
    "entityName": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [
                "0000320193-25-000001",
                "0000320193-26-000001",
            ],
            "filingDate": ["2025-01-15", "2026-01-14"],
            "reportDate": ["2024-12-28", "2025-12-27"],
            "form": ["10-K", "10-K"],
            "primaryDocument": ["aapl-20241228.htm", "aapl-20251227.htm"],
        },
        "files": [],
    },
}

HISTORICAL_FILE_NAME = "CIK0000320193-submissions-001.json"
HISTORICAL_FILE = {
    "cik": "320193",
    "filings": {
        "recent": {
            "accessionNumber": ["0000320193-15-000010"],
            "filingDate": ["2015-10-28"],
            "reportDate": ["2015-09-26"],
            "form": ["10-K"],
            "primaryDocument": ["aapl-20150926.htm"],
        },
        "files": [],
    },
}

HISTORICAL_SUBMISSIONS = {
    "cik": "320193",
    "entityName": "Apple Inc.",
    "filings": {
        "recent": {
            "accessionNumber": [],
            "filingDate": [],
            "reportDate": [],
            "form": [],
            "primaryDocument": [],
        },
        "files": [{"name": HISTORICAL_FILE_NAME}],
    },
}

HTML_DOC = """<!DOCTYPE html>
<html>
<head>
  <title>Apple Inc. 10-K</title>
  <style>.hidden { display: none; }</style>
  <script>alert('do-not-extract')</script>
</head>
<body>
  <h1>Apple Inc. Annual Report</h1>
  <h2>Financial Statements</h2>
  <p>Revenue was 100 billion dollars.</p>
  <ul>
    <li>First item</li>
    <li>Second item</li>
  </ul>
  <table>
    <tr><th>Year</th><th>Revenue</th></tr>
    <tr><td>2025</td><td>100</td></tr>
  </table>
</body>
</html>
"""

XML_DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<root>
  <title>XBRL Report</title>
  <p>Net income was 42 million.</p>
</root>
"""


class FakeClient:
    def __init__(
        self,
        ticker_map=None,
        submissions=None,
        history=None,
        document=HTML_DOC.encode("utf-8"),
        content_type="text/html",
    ):
        self.ticker_map = ticker_map if ticker_map is not None else TICKER_MAP
        self.submissions = (
            submissions if submissions is not None else AAPL_SUBMISSIONS
        )
        self.history = history or {}
        self.document = document
        self.content_type = content_type
        self.document_calls = []
        self.submissions_calls = []

    def get_ticker_map(self):
        return self.ticker_map

    def get_submissions(self, cik):
        self.submissions_calls.append(cik)
        return self.submissions

    def get_submissions_file(self, name):
        return self.history[name]

    def get_document(self, url):
        self.document_calls.append(url)
        return self.document, self.content_type
