# Instructions: Sentence-Level SEC Embeddings with Hierarchy Metadata

## Objective
Implement sentence-level content records for SEC filing HTML. Every meaningful sentence in ordinary prose should have its own record and embedding, with its original text, filing metadata, source location, and an ordered array of all active ancestor headings. Do not split on visual line wraps.

This task covers DOM structure parsing, hierarchy tracking, sentence segmentation, record construction, anchor relationships, and offline tests. Do not implement SEC URL resolution/download, object storage, live embedding API calls, or Qdrant setup unless required by existing interfaces.

## Example hierarchy
For:
- PART I
  - Item 1. Business
    - Products
      - iPhone
        - "iPhone is the Company's line of smartphones."
        - "The iPhone line includes several models."

Store the hierarchy for each sentence as:
`["PART I", "Item 1. Business", "Products", "iPhone"]`

## Record schema
Adapt to existing project conventions, but preserve these fields:

```json
{
  "point_id": "stable-deterministic-id",
  "document_id": "aapl_2025_10k",
  "ticker": "AAPL",
  "cik": "320193",
  "form_type": "10-K",
  "accession_number": "0000320193-25-000079",
  "part": "PART I",
  "item_id": "1",
  "item_title": "Business",
  "hierarchy": ["PART I", "Item 1. Business", "Products", "iPhone"],
  "unit_type": "sentence",
  "text": "iPhone is the Company's line of smartphones.",
  "embedding_text": "PART I > Item 1. Business > Products > iPhone\n\niPhone is the Company's line of smartphones.",
  "order_index": 42,
  "sentence_index_in_block": 0,
  "source_dom_id": null,
  "anchor_id": null,
  "page_number": null,
  "source_url": "https://www.sec.gov/Archives/...",
  "embedding_model": "model-name-and-version",
  "parser_version": "1"
}
```

The values are illustrative. `text` is the original normalized sentence and must not contain the hierarchy prefix. `embedding_text` is the input used to generate the vector. Store the vector in the vector database's vector field (for Qdrant, the point vector); store `text`, `hierarchy`, and metadata in the payload. Do not duplicate large vectors inside payload by default.

## DOM parsing and hierarchy rules
SEC HTML may use generic `div` and `span` tags with inline CSS instead of semantic headings. Traverse the DOM in document order and maintain:
- `current_part`
- `current_item_id`
- `current_item_title`
- `hierarchy` (ordered array of active heading labels)
- `order_index`

Rules:
1. Recognize standalone `PART I`, `PART II`, etc. Update the part and reset lower-level context as appropriate.
2. Recognize standalone SEC item headings such as `Item 1. Business`, `Item 1A. Risk Factors`, `Item 7...`, and `Item 8...`. Update item fields and clear prior subsection headings.
3. Detect subsection headings using semantic tags/explicit markup first, known SEC patterns second, and standalone block structure + font weight/size/style + spacing/indentation third.
4. Do not classify every bold or italic phrase as a heading; bold/italic text can appear in prose and tables. For uncertain blocks, preserve the text as content rather than corrupting hierarchy.
5. When a heading is detected, update the ordered hierarchy array at the appropriate depth. When a sibling heading appears, replace the old sibling and remove deeper levels. Example: `Products > iPhone` changes to `Products > Mac` when `Mac` is a sibling.
6. When a new SEC Item begins, do not retain headings from the previous item.
7. Avoid duplicate text from nested DOM elements. Do not treat table-of-contents entries as body headings when they can be distinguished.
8. Preserve DOM IDs/anchors and original document order where available.

## Sentence segmentation
- Split ordinary prose into one record per sentence by default.
- Use a sentence tokenizer or a carefully tested fallback. SEC text contains abbreviations, decimals, dates, citations, and company names; test these.
- Normalize whitespace carefully, but preserve sentence wording.
- Do not split at every newline or visual line break.
- Do not sentence-split tables blindly. Represent tables as structured table units or coherent row groups, retaining column headers, row labels, values, units, reporting periods, and footnotes.
- Lists may be stored one record per list item with the same hierarchy context.
- Headings should normally live in the hierarchy array; optionally create separate heading records for navigation.
- Keep sentences within their own heading/item context; never combine across different SEC Items.
- Document the policy for short fragments that are not full sentences; either keep them as typed fragments or combine only with a related sentence from the same paragraph.

## Embedding strategy
For each sentence:
1. Store the original sentence in `text`.
2. Build `embedding_text` from the ordered hierarchy labels plus the sentence, e.g. `PART I > Item 1. Business > Products > iPhone`, blank line, then the sentence.
3. Generate one embedding from `embedding_text` by default. Make this strategy configurable and versioned.
4. Store the vector separately in the vector field; payload should include the original text, hierarchy array, and metadata.
5. On retrieval, return `text` and `hierarchy`; do not present `embedding_text` as if it were original source text.

## Hyperlinks and anchors
Store links separately with `source_unit_id` where possible, `link_text`, `href`, normalized `target_anchor`, and resolved `target_unit_id`.
- Resolve same-document links such as `href="#section-id"`.
- Multiple links may point to one anchor: store each link relationship, but store destination sentence records once.
- Link text can be a retrieval alias; it must not replace the canonical heading or cause duplicate vectors.
- Treat external links separately. Missing targets must not fail the entire ingestion.

## Page numbers
HTML does not inherently provide stable printed page numbers. Populate `page_number` only when a reliable source marker exists or pagination is computed using a documented rendering method. Otherwise use null and preserve `order_index`, DOM IDs, and anchors.

## Stable IDs
Generate deterministic IDs from filing identity plus stable source identity and sentence position/content hash. Prefer a stable DOM ID/anchor plus sentence index; otherwise use accession number, deterministic block/sentence order, and normalized text hash. Record parser and embedding strategy versions.

## Required offline tests
Use `pytest` and local fixtures; no live SEC or embedding API requests.
1. Recognize PART I and PART II.
2. Recognize Item 1, Item 1A, Item 7, and Item 8.
3. Normalize whitespace and non-breaking spaces in heading labels.
4. Assign sentences to the correct part/item.
5. Build nested hierarchy arrays.
6. Replace sibling headings and clear deeper headings correctly.
7. Ensure Item 1A clears Item 1 subsection context.
8. Split prose into sentences without treating visual line wraps as sentence boundaries.
9. Handle abbreviations, dates, and decimals in test fixtures.
10. Keep `text` separate from `embedding_text`.
11. Ensure every record has required metadata and hierarchy.
12. Preserve tables and lists as structured units.
13. Do not misclassify bold text inside prose or every italic phrase as a heading.
14. Resolve intra-document links and support many links to one anchor without duplicating target records.
15. Preserve DOM IDs/anchors where available.
16. Do not invent page numbers.
17. Produce deterministic IDs across repeated runs.
18. Handle empty/malformed HTML gracefully.
19. Mock the embedding function and verify vector/payload separation.
20. Ensure all tests pass offline.

## Definition of done
- Every prose sentence has its own deterministic content record and vector-generation input.
- Each record contains the exact source sentence and complete ordered `hierarchy` array.
- `embedding_text` is separate from original `text`.
- Vectors are stored in the vector field, not duplicated in payload by default.
- Tables, lists, anchors, and page-location uncertainty are handled as specified.
- `python -m pytest -q` passes from the repository root.
- Normal tests make no SEC or embedding API requests.

## Completion report
Report files changed, hierarchy detection rules, sentence splitting strategy, embedding input strategy, vector/payload schema, table and anchor handling, exact test command and pass/fail/skip counts, limitations, and confirmation that tests ran offline.
