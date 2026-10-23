"""Offline fixtures for the structure-aware sentence index tests."""

from sentence_records import FilingMeta

META = FilingMeta(
    document_id="fixture_2025_10k",
    ticker="AAPL",
    cik="320193",
    form_type="10-K",
    accession_number="0000320193-25-000079",
    source_url="https://www.sec.gov/Archives/edgar/data/320193/000032019325000079/aapl-20250927.htm",
    company="Apple Inc.",
    filing_date="2025-10-31",
    report_date="2025-09-27",
)

SEC_STYLE_HTML = """
<html>
<head>
  <title>Apple Inc. 2025 Form 10-K</title>
  <style>.hidden { display: none; }</style>
  <script>var ignored = "script text";</script>
</head>
<body style="font-size:10pt">
  <div id="toc" class="table-of-contents">
    <div><a href="#products">Products</a> ........ 12</div>
    <div><a href="#iphone">iPhone</a> ........ 13</div>
    <div><a href="#risk-factors">Risk Factors</a> ........ 20</div>
  </div>

  <div style="font-size:14pt;font-weight:bold">PART I</div>
  <div style="font-size:14pt;font-weight:bold">Item 1. Business</div>
  <p>Apple Inc. designs and markets smartphones. The Company also sells services worldwide.</p>

  <div id="products" style="font-size:12pt;font-weight:bold">Products</div>
  <p>The Company offers a range of products. See the <a href="#iphone">iPhone</a> page,
     the <a href="#iphone">iPhone overview</a>, <a href="https://www.apple.com">Apple's website</a>,
     and the <a href="#missing-target">missing page</a>.</p>

  <div id="iphone" style="font-size:10pt;font-weight:bold">iPhone</div>
  <p id="iphone-detail">iPhone is the Company's line of smartphones. It is sold worldwide.</p>
  <p><span>Nested inline text</span> appears once in this paragraph.</p>

  <div style="font-style:italic">This italic sentence explains seasonality in ordinary prose and should remain content.</div>
  <p>The Company <b>believes</b> this bold word belongs to prose. It is not a heading.</p>

  <table id="sales-table">
    <caption>Net sales by product</caption>
    <thead><tr><th>Product</th><th>2025</th><th>2024</th></tr></thead>
    <tbody>
      <tr><td><a href="#iphone">iPhone</a></td><td>100</td><td>90</td></tr>
      <tr><td>Services</td><td>50</td><td>40</td></tr>
    </tbody>
  </table>

  <ul id="product-list">
    <li>First product sentence. Second product sentence.</li>
    <li>Second item with a nested list.<ul><li>Child product sentence.</li></ul></li>
  </ul>

  <div>Page 42</div>
  <div style="display:none">Hidden display text.</div>
  <div hidden>Hidden attribute text.</div>
  <ix:header>Hidden XBRL header text.</ix:header>
  <script>another hidden script</script>

  <a id="risk-factors" name="risk-factors"></a>
  <div style="font-size:14pt;font-weight:bold">Item 1A. Risk Factors</div>
  <p>Risks include competition. Risks also include regulation.</p>

  <div style="font-size:14pt;font-weight:bold">PART II</div>
  <div style="font-size:14pt;font-weight:bold">Item 7. Management's Discussion and Analysis</div>
  <p>Revenue increased in 2025. Margins remained stable. Read the
     <a href="#risk-factors">risk factors</a> for context.</p>

  <div style="font-size:14pt;font-weight:bold">Item 8. Financial Statements</div>
  <p>The consolidated statements follow.</p>
  <table>
    <tr><th>Line</th><th>Amount</th></tr>
    <tr><td>Cash</td><td>1000</td></tr>
  </table>
</body>
</html>
"""

SEMANTIC_HTML = """
<html><body>
  <h1>PART I</h1>
  <h2>Item 1. Business</h2>
  <h3>Overview</h3>
  <p>Overview sentence.</p>
  <h3>Products</h3>
  <p>Products sentence.</p>
  <h4>iPhone</h4>
  <p>iPhone sentence.</p>
  <h2>Item 1A. Risk Factors</h2>
  <p>Risk sentence.</p>
</body></html>
"""

NBSP_HTML = """
<html><body style="font-size:10pt">
  <div style="font-size:14pt;font-weight:bold">PART&nbsp;I</div>
  <div style="font-size:14pt;font-weight:bold">Item&nbsp;1A.&nbsp;Risk&nbsp;Factors</div>
  <p>Body sentence.</p>
</body></html>
"""

LONG_PARAGRAPH_HTML = """
<html><body style="font-size:10pt">
  <div style="font-size:14pt;font-weight:bold">PART I</div>
  <div style="font-size:14pt;font-weight:bold">Item 1. Business</div>
  <p>First sentence. Second sentence! Third sentence? A fourth sentence follows.</p>
</body></html>
"""


def large_table_html(rows: int = 100) -> str:
    body_rows = "".join(
        f"<tr><td>Row {index}</td><td>{index * 10}</td></tr>"
        for index in range(1, rows + 1)
    )
    return f"""
<html><body style="font-size:10pt">
  <div style="font-size:14pt;font-weight:bold">PART I</div>
  <div style="font-size:14pt;font-weight:bold">Item 8. Financial Statements</div>
  <table id="large-table">
    <thead><tr><th>Row</th><th>Value</th></tr></thead>
    <tbody>{body_rows}</tbody>
  </table>
</body></html>
"""


MALFORMED_HTML = "<div><p>Unclosed <b>bold text <table><tr><td>cell"
EMPTY_HTML = "   "
