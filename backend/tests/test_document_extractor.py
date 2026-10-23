import pytest

from document_extractor import EmptyExtractionError, extract_document
from tests.fixtures import HTML_DOC, XML_DOC


def extract_html(content=HTML_DOC, content_type="text/html"):
    return extract_document(content.encode("utf-8"), content_type, "https://example.test/doc")


def test_extraction_preserves_headings_paragraphs_lists_and_tables():
    result = extract_html()
    text = result.text

    assert "Apple Inc. Annual Report" in text
    assert "Financial Statements" in text
    assert "Revenue was 100 billion dollars." in text
    assert "First item" in text
    assert "Second item" in text
    assert "Year" in text
    assert "2025" in text
    assert "100" in text
    assert result.title == "Apple Inc. 10-K"
    assert result.content_type == "text/html"
    assert result.source_url == "https://example.test/doc"


def test_boilerplate_is_removed():
    text = extract_html().text
    assert "do-not-extract" not in text
    assert "display: none" not in text
    assert "alert" not in text


def test_whitespace_is_readable_not_one_token_stream():
    text = extract_html().text
    assert "\n" in text
    assert "Revenue was 100 billion dollars. First item" not in text
    assert len(text.split()) > 10


def test_empty_document_raises():
    with pytest.raises(EmptyExtractionError):
        extract_document(b"<html><body></body></html>", "text/html", "url")


def test_script_and_style_only_document_raises():
    content = b"<html><script>alert(1)</script><style>.x{}</style></html>"
    with pytest.raises(EmptyExtractionError):
        extract_document(content, "text/html", "url")


def test_xml_document_is_parsed():
    result = extract_document(XML_DOC, "application/xml", "url")
    assert "Net income was 42 million." in result.text
    assert result.content_type == "application/xml"


def test_inline_xbrl_header_is_excluded():
    content = b"""<html><body>
    <ix:header><ix:hidden>hidden-context-123</ix:hidden></ix:header>
    <p>Visible revenue text for the year.</p>
    </body></html>"""
    text = extract_document(content, "text/html", "url").text
    assert "hidden-context-123" not in text
    assert "Visible revenue text for the year." in text


def test_blank_bytes_raise():
    with pytest.raises(EmptyExtractionError):
        extract_document(b"   \n  ", "text/html", "url")


def test_near_empty_document_raises():
    with pytest.raises(EmptyExtractionError):
        extract_document(b"<html><body><p>hi</p></body></html>", "text/html", "url")
