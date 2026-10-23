from retrieval_service import RetrievedChunk, build_sources


def chunk(text: str, payload: dict, point_id: str = "p1") -> RetrievedChunk:
    return RetrievedChunk(
        point_id=point_id,
        text=text,
        vector_score=0.5,
        rerank_score=0.9,
        payload=payload,
    )


def base_payload(**overrides):
    payload = {
        "ticker": "META",
        "form_type": "10-K",
        "year": 2025,
        "source_url": "https://www.sec.gov/Archives/meta.htm",
        "hierarchy": ["PART II", "Item 7", "Revenue"],
        "unit_type": "paragraph",
    }
    payload.update(overrides)
    return payload


def test_source_uses_dom_anchor_when_available():
    source = build_sources(
        [
            chunk(
                "Total revenue was $200.97 billion.",
                base_payload(anchor_id="revenue-section"),
            )
        ]
    )[0]

    assert source["url"].endswith("#revenue-section")
    assert source["document_url"] == "https://www.sec.gov/Archives/meta.htm"
    assert source["link_type"] == "anchor"


def test_source_uses_text_fragment_for_prose():
    source = build_sources(
        [
            chunk(
                "Total revenue for 2025 was $200.97 billion, an increase of 22% "
                "compared to 2024.",
                base_payload(),
            )
        ]
    )[0]

    assert source["link_type"] == "text_fragment"
    assert "#:~:text=" in source["url"]
    assert "Total%20revenue%20for%202025" in source["url"]
    assert "%24200.97" in source["url"]


def test_source_uses_table_value_for_deep_link():
    source = build_sources(
        [
            chunk(
                "| Column 1 | Column 2 |\n| --- | --- |\n"
                "| Advertising | $ | 196,175 |\n"
                "| Total revenue | $ | 200,966 |",
                base_payload(unit_type="table"),
            )
        ]
    )[0]

    assert source["link_type"] == "text_fragment"
    assert source["url"].endswith("#:~:text=200%2C966")


def test_source_falls_back_to_heading_for_short_table():
    source = build_sources(
        [
            chunk(
                "| Column 1 | Column 2 |",
                base_payload(unit_type="table"),
            )
        ]
    )[0]

    assert source["link_type"] == "text_fragment"
    assert source["url"].endswith("#:~:text=Revenue")


def test_source_preserves_existing_fragment():
    source = build_sources(
        [
            chunk(
                "Total revenue was $200.97 billion.",
                base_payload(
                    source_url="https://www.sec.gov/Archives/meta.htm#existing",
                ),
            )
        ]
    )[0]

    assert source["url"].endswith("#existing")
    assert source["document_url"] == "https://www.sec.gov/Archives/meta.htm"
    assert source["link_type"] == "anchor"


def test_source_without_url_is_graceful():
    source = build_sources(
        [chunk("Some content.", base_payload(source_url=""))]
    )[0]

    assert source["url"] == ""
    assert source["document_url"] == ""
    assert source["link_type"] == "document"
