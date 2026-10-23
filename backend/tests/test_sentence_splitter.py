from sentence_splitter import normalize_text, split_sentences


def test_normalize_whitespace_and_nbsp():
    assert normalize_text("  Apple\u00a0Inc.\n sells\tphones  ") == "Apple Inc. sells phones"


def test_split_basic_sentences():
    assert split_sentences("First sentence. Second sentence! Third sentence?") == [
        "First sentence.",
        "Second sentence!",
        "Third sentence?",
    ]


def test_abbreviations_do_not_split():
    sentences = split_sentences(
        "Apple Inc. sells phones. Dr. Smith works there. The Company is based in the U.S."
    )
    assert len(sentences) == 3
    assert sentences[0] == "Apple Inc. sells phones."
    assert sentences[1] == "Dr. Smith works there."
    assert sentences[2].endswith("U.S.")


def test_decimal_values_do_not_split():
    sentences = split_sentences("Revenue was $3.5 billion. It grew by 2.1 percent.")
    assert len(sentences) == 2


def test_visual_line_wraps_are_joined():
    sentences = split_sentences("First sentence.\nSecond sentence.\nThird sentence.")
    assert sentences == ["First sentence.", "Second sentence.", "Third sentence."]


def test_item_labels_are_protected():
    sentences = split_sentences("Item 8. Financial Statements are included below.")
    assert len(sentences) == 1


def test_empty_text():
    assert split_sentences("") == []
    assert split_sentences("   ") == []
