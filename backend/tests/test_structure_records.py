import pytest

from sentence_records import build_embedding_text, parse_filing_html
from tests.structure_fixtures import (
    EMPTY_HTML,
    LONG_PARAGRAPH_HTML,
    MALFORMED_HTML,
    META,
    NBSP_HTML,
    SEC_STYLE_HTML,
    SEMANTIC_HTML,
    large_table_html,
)

MODEL = "text-embedding-3-large"


@pytest.fixture(scope="module")
def parsed():
    return parse_filing_html(SEC_STYLE_HTML, META, embedding_model=MODEL)


def _by_text(parsed, prefix):
    return next(record for record in parsed.records if record.text.startswith(prefix))


def test_parts_are_recognized(parsed):
    assert {record.part for record in parsed.records} == {"PART I", "PART II"}


def test_items_are_recognized(parsed):
    titles = {}
    for record in parsed.records:
        titles[record.item_id] = record.item_title
    assert titles == {
        "1": "Business",
        "1A": "Risk Factors",
        "7": "Management's Discussion and Analysis",
        "8": "Financial Statements",
    }


def test_nbsp_labels_are_normalized():
    parsed = parse_filing_html(NBSP_HTML, META)
    assert parsed.records
    assert all(record.part == "PART I" for record in parsed.records)
    assert all(record.item_id == "1A" for record in parsed.records)
    assert all(record.item_title == "Risk Factors" for record in parsed.records)


def test_paragraphs_are_assigned_to_active_item(parsed):
    assert _by_text(parsed, "Apple Inc. designs").item_id == "1"
    assert _by_text(parsed, "Risks include competition").item_id == "1A"
    assert _by_text(parsed, "Revenue increased").item_id == "7"
    assert _by_text(parsed, "The consolidated statements").item_id == "8"


def test_nested_heading_path(parsed):
    product = _by_text(parsed, "The Company offers")
    iphone = _by_text(parsed, "iPhone is the Company's")
    assert product.hierarchy == [
        "PART I",
        "Item 1 \u2014 Business",
        "Products",
    ]
    assert iphone.hierarchy == [
        "PART I",
        "Item 1 \u2014 Business",
        "Products",
        "iPhone",
    ]


def test_part_resets_subsection_context(parsed):
    risk = _by_text(parsed, "Risks include competition")
    assert risk.hierarchy == ["PART I", "Item 1A \u2014 Risk Factors"]


def test_bold_prose_is_not_a_heading(parsed):
    record = _by_text(parsed, "The Company believes")
    assert record.hierarchy[-1] == "iPhone"
    assert "believes" not in record.hierarchy
    assert "It is not a heading." in [r.text for r in parsed.records]


def test_italic_prose_is_not_a_heading(parsed):
    record = _by_text(parsed, "This italic sentence")
    assert record.unit_type == "paragraph"
    assert record.hierarchy[-1] == "iPhone"


def test_document_order_is_preserved(parsed):
    assert [record.order_index for record in parsed.records] == list(
        range(len(parsed.records))
    )


def test_nested_inline_text_is_not_duplicated(parsed):
    matches = [
        record
        for record in parsed.records
        if "Nested inline text" in record.text
    ]
    assert len(matches) == 1
    assert matches[0].text == "Nested inline text appears once in this paragraph."


def test_scripts_styles_and_hidden_content_are_excluded(parsed):
    joined = "\n".join(record.text for record in parsed.records)
    for hidden in (
        "script text",
        "another hidden script",
        "Hidden display text",
        "Hidden attribute text",
        "Hidden XBRL header text",
    ):
        assert hidden not in joined


def test_table_headers_values_and_caption(parsed):
    table = next(record for record in parsed.records if record.unit_type == "table")
    assert table.source_dom_id == "sales-table"
    assert table.text.startswith("Net sales by product")
    assert "| Product | 2025 | 2024 |" in table.text
    assert "| iPhone | 100 | 90 |" in table.text
    assert "| Services | 50 | 40 |" in table.text
    assert table.hierarchy[-1] == "iPhone"


def test_large_table_splits_into_header_repeated_groups():
    parsed = parse_filing_html(large_table_html(100), META)
    tables = [record for record in parsed.records if record.unit_type == "table"]
    assert len(tables) == 3
    assert [record.table_group_index for record in tables] == [0, 1, 2]
    assert len({record.table_index for record in tables}) == 1
    for group in tables:
        assert "| Row | Value |" in group.text
    assert "Row 1" in tables[0].text
    assert "Row 100" in tables[-1].text


def test_internal_anchor_links_resolve(parsed):
    link = next(link for link in parsed.links if link.link_text == "iPhone")
    assert link.external is False
    assert link.target_anchor == "iphone"
    assert link.resolved is True
    assert link.target_unit_id in {record.point_id for record in parsed.records}


def test_multiple_labels_share_one_anchor_target(parsed):
    table = next(record for record in parsed.records if record.unit_type == "table")
    links = [
        link
        for link in parsed.links
        if link.href == "#iphone" and link.source_unit_id != table.point_id
    ]
    assert {link.link_text for link in links} == {"iPhone", "iPhone overview"}
    assert len({link.target_unit_id for link in links}) == 1
    destination = _by_text(parsed, "iPhone is the Company's")
    assert links[0].target_unit_id == destination.point_id
    assert sum(record.text.startswith("iPhone is the Company's") for record in parsed.records) == 1


def test_missing_anchor_target_is_handled(parsed):
    link = next(link for link in parsed.links if link.link_text == "missing page")
    assert link.target_anchor == "missing-target"
    assert link.resolved is False
    assert link.target_unit_id is None


def test_external_links_are_separate(parsed):
    link = next(link for link in parsed.links if link.link_text == "Apple's website")
    assert link.external is True
    assert link.resolved is False
    assert link.target_anchor is None
    assert link.target_unit_id is None


def test_source_ids_and_anchor_names_are_preserved(parsed):
    assert parsed.anchor_targets["products"]
    assert parsed.anchor_targets["iphone"] == parsed.anchor_targets["iphone-detail"]
    assert parsed.anchor_targets["risk-factors"]
    assert parsed.anchor_targets["sales-table"]
    record = _by_text(parsed, "iPhone is the Company's")
    assert record.source_dom_id == "iphone-detail"
    table = next(item for item in parsed.records if item.unit_type == "table")
    assert table.source_dom_id == "sales-table"


def test_year_is_recorded_instead_of_page_numbers(parsed):
    assert all(record.year == 2025 for record in parsed.records)
    assert not any(hasattr(record, "page_number") for record in parsed.records)
    assert "Page 42" not in "\n".join(record.text for record in parsed.records)


def test_long_paragraph_splits_at_sentence_boundaries():
    parsed = parse_filing_html(LONG_PARAGRAPH_HTML, META)
    assert [record.text for record in parsed.records] == [
        "First sentence.",
        "Second sentence!",
        "Third sentence?",
        "A fourth sentence follows.",
    ]
    assert {record.sentence_index_in_block for record in parsed.records} == {0, 1, 2, 3}
    assert {record.hierarchy[-1] for record in parsed.records} == {
        "Item 1 \u2014 Business"
    }


def test_chunks_do_not_cross_item_boundaries(parsed):
    risk = _by_text(parsed, "Risks also include regulation")
    revenue = _by_text(parsed, "Revenue increased")
    assert risk.item_id == "1A"
    assert revenue.item_id == "7"
    assert "Revenue" not in risk.text
    assert "Risks" not in revenue.text


def test_content_and_embedding_text_are_separate(parsed):
    for record in parsed.records[:10]:
        assert record.text in record.embedding_text
        assert record.embedding_text == build_embedding_text(
            record.hierarchy, record.text
        )
        assert record.embedding_text.startswith(" > ".join(record.hierarchy) + "\n\n")
        assert not record.text.startswith("PART I >")


def test_chunk_ids_are_deterministic():
    first = parse_filing_html(SEC_STYLE_HTML, META)
    second = parse_filing_html(SEC_STYLE_HTML, META)
    assert [record.point_id for record in first.records] == [
        record.point_id for record in second.records
    ]
    assert len({record.point_id for record in first.records}) == len(first.records)


def test_empty_and_malformed_html_are_graceful():
    assert parse_filing_html(EMPTY_HTML, META).records == []
    malformed = parse_filing_html(MALFORMED_HTML, META)
    assert all(record.document_id == META.document_id for record in malformed.records)


def test_every_record_has_required_metadata(parsed):
    assert parsed.records
    for record in parsed.records:
        assert record.point_id
        assert record.document_id == META.document_id
        assert record.ticker == META.ticker
        assert record.cik == META.cik
        assert record.form_type == META.form_type
        assert record.accession_number == META.accession_number
        assert record.source_url == META.source_url
        assert isinstance(record.order_index, int)
        assert record.parser_version
        assert record.embedding_strategy == "hierarchy-prefix-v1"


def test_semantic_headings_are_honored():
    parsed = parse_filing_html(SEMANTIC_HTML, META)
    iphone = _by_text(parsed, "iPhone sentence.")
    assert iphone.hierarchy == [
        "PART I",
        "Item 1 \u2014 Business",
        "Products",
        "iPhone",
    ]


def test_toc_entries_are_not_body_records(parsed):
    text = "\n".join(record.text for record in parsed.records)
    assert "..." not in text
    assert parsed.records[0].part == "PART I"
    assert not any(
        record.hierarchy == ["Products"] or record.text == "Products"
        for record in parsed.records
    )


def test_list_items_and_nested_lists(parsed):
    lists = [record for record in parsed.records if record.unit_type == "list_item"]
    texts = [record.text for record in lists]
    assert "First product sentence." in texts
    assert "Second product sentence." in texts
    assert "Child product sentence." in texts
    assert len(lists) == 4
    assert all(record.hierarchy[-1] == "iPhone" for record in lists)


def test_table_links_point_at_the_table_record(parsed):
    table = next(record for record in parsed.records if record.unit_type == "table")
    link = next(
        link
        for link in parsed.links
        if link.href == "#iphone" and link.source_unit_id == table.point_id
    )
    assert link.link_text == "iPhone"
    assert link.resolved is True


def test_optional_heading_records():
    default = parse_filing_html(SEC_STYLE_HTML, META)
    with_headings = parse_filing_html(
        SEC_STYLE_HTML, META, include_heading_records=True
    )
    assert not any(record.unit_type == "heading" for record in default.records)
    headings = [record for record in with_headings.records if record.unit_type == "heading"]
    assert {record.text for record in headings} >= {"PART I", "PART II", "Products", "iPhone"}
