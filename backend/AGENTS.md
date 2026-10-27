# Instructions: Structure-Aware Chunking for SEC Filing HTML

## Objective

Implement a deterministic, structure-aware chunking stage for SEC Form 10-K/10-Q HTML documents. The input is the output of the SEC URL resolution and HTML extraction stage. The output must preserve the filing hierarchy and produce content units suitable for embedding and retrieval.

This task covers document structure detection, content-unit creation, metadata propagation, hyperlink/anchor mapping, and chunk generation. Do not implement SEC ticker lookup, SEC HTTP downloading, embedding generation, Qdrant indexing, or object-storage uploads unless an existing project interface explicitly requires an adapter.

## Core design principle

Do not flatten the entire HTML into one string before interpreting its structure. Parse the DOM in document order, preserve structural signals, then create chunks from recognized content blocks.

SEC HTML may use generic `div` and `span` elements with inline styles instead of semantic heading tags. Use multiple signals together. Do not assume every bold or italic element is a heading.

## Input contract

Consume raw HTML plus filing metadata, or the parsed document object produced by the upstream loader. Reuse the existing interface rather than downloading the filing again.

Metadata should include when available: `document_id`, `ticker`, `cik`, `form_type`, `accession_number`, `filing_date`, `report_date`, and `source_url`.

## Required hierarchy

Preserve a hierarchy such as:

- Filing
  - PART I
    - Item 1 — Business
      - Company Background
        - paragraphs
      - Products
        - iPhone
          - paragraphs
        - Mac
          - paragraphs
    - Item 1A — Risk Factors
      - subsections
        - paragraphs
  - PART II
    - Item 7 — Management's Discussion and Analysis
    - Item 8 — Financial Statements

Do not hardcode Apple's headings or assume all filings have identical structures.

## Parsing and classification requirements

### 1. DOM traversal

- Traverse relevant HTML elements in document order.
- Preserve source order and useful DOM identifiers.
- Prefer semantic headings when present.
- For SEC-style `div`/`span` markup, inspect block boundaries, text, CSS classes, inline styles, font weight, font style, font size, indentation, spacing, and alignment.
- Avoid double-counting text when parent and child elements contain the same content.
- Exclude scripts and styles, but do not remove filing content just because it is inside a table.
- Avoid processing table-of-contents entries as body sections. Use link targets, anchors, and document order to distinguish navigation from actual body content when possible.

### 2. Parts and SEC Items

Recognize `PART I`, `PART II`, etc., and labels such as `Item 1. Business`, `Item 1A. Risk Factors`, `Item 7. Management's Discussion and Analysis`, and `Item 8. Financial Statements`.

Normalize whitespace and non-breaking spaces before matching. Use standalone-block and structural signals; do not infer an item from a substring in ordinary prose.

On a part heading, update `current_part` and reset item/subsection context as appropriate. On an item heading, update `current_item_id` and `current_item_title`, then reset the subsection heading path.

### 3. Subsection headings

Use signals in this order:
1. Semantic heading tags or explicit structural markup.
2. Recognized filing-specific labels and patterns.
3. Standalone blocks with bold styling, appropriate font size, spacing, and position.
4. Italic standalone labels as possible lower-level headings, only when context supports it.

Do not treat all bold or italic text as headings. Bold text can occur in prose and tables. If uncertain, preserve the text as content and mark `unit_type="unknown"` or record classification confidence rather than corrupting the hierarchy.

When a block is a heading, update `heading_path` at the appropriate depth. Preserve its original text. By default, include heading context in descendant chunks rather than creating an embedding for every heading alone. A heading-only node may be retained for navigation.

### 4. Paragraphs and content units

For each content block preserve original text, part/item context, heading path, source order, DOM ID/anchor if available, and `unit_type` (`paragraph`, `heading`, `table`, `list`, or `unknown`). Do not treat visual line wraps as separate semantic chunks.

### 5. Hyperlinks and anchors

Extract intra-document links separately from content units. Store `document_id`, source unit/DOM ID if available, `link_text`, `href`, normalized `target_anchor`, and resolved `target_unit_id` when possible.

- Resolve same-document links such as `href="#section-id"` to the element with that ID.
- Support multiple links to one target as a many-to-one relationship; do not duplicate target content or embeddings.
- Keep link text as a possible retrieval alias, but do not replace the canonical destination heading.
- Retain external links as external references.
- Handle missing or invalid targets gracefully.

### 6. Page numbers and source locations

HTML does not inherently provide stable printed page numbers. Set `page_number` only if the HTML has a reliable page marker or your application derives pagination through a documented rendering method. Validate markers such as `hr` with `page-break-after: always` before treating them as pages. Always preserve `order_index` and DOM/anchor identifiers as fallback locations.

### 7. Tables

- Detect tables and extract rows, columns, headers, values, and captions/footnotes where possible.
- Preserve table structure in Markdown or a structured row/cell payload.
- Include current part, item, heading path, caption/heading, and reporting period where available.
- Avoid one embedding per individual cell by default.
- Prefer a table-level representation or coherent row groups. For large tables, split into row groups while repeating headers and table context.
- Handle nested tables and Inline XBRL markup carefully to avoid duplicated cell text.
- If table extraction fails, preserve readable fallback text and report the issue.

### 8. Chunking policy

- Default to paragraph-aware chunks, not one embedding per visual line.
- Keep short, coherent paragraphs together if they share a subsection and fit the configured size limit.
- Split oversized paragraphs at sentence boundaries when possible; use token/character limits as fallback.
- Never merge content across different SEC Item boundaries or unrelated subsection headings.
- Keep lists together where practical; split exceptionally long lists into coherent groups.
- Use configurable chunk size and overlap. Prefer token-based sizing if the embedding tokenizer is available; document the unit used.
- Avoid excessive overlap and near-duplicate vectors.
- Add a contextual prefix to `embedding_text`, e.g. `PART I > Item 1 — Business > Products > iPhone`, followed by content.
- Keep `content_text` separate from `embedding_text` so the original source text remains available.
- Preserve deterministic order and stable IDs for idempotent ingestion.
- Do not create an embedding for every sentence/line by default; evaluate sentence-level embeddings later if retrieval tests justify them.

## Suggested output schema

Adapt to repository conventions. Each chunk should include at least:

```json
{
  "chunk_id": "stable-deterministic-id",
  "document_id": "aapl_2025_10k",
  "ticker": "AAPL",
  "cik": "320193",
  "form_type": "10-K",
  "accession_number": "0000320193-25-000079",
  "part": "PART I",
  "item_id": "1",
  "item_title": "Business",
  "heading_path": ["Products", "iPhone"],
  "unit_type": "paragraph",
  "content_text": "iPhone is the Company's line of smartphones...",
  "embedding_text": "PART I > Item 1 — Business > Products > iPhone\n\niPhone is the Company's line of smartphones...",
  "order_index": 42,
  "anchor_id": null,
  "page_number": null,
  "source_url": "https://www.sec.gov/Archives/..."
}
```

Example values are illustrative only. Production metadata must come from the actual filing. Also produce a document-level hierarchy/manifest and anchor-to-unit mapping if useful.

## Stable IDs and idempotency

- Derive chunk IDs deterministically from stable document identity plus source location/content identity, such as accession number, DOM ID/anchor, unit order, and content hash.
- Do not rely only on array index if minor parser changes could shift later indexes.
- If no stable DOM IDs exist, use a deterministic sequence plus normalized content hash.
- Record parser/chunker version or configuration so reprocessing after rule changes is observable.

## Required offline tests

Use `pytest` with small HTML fixtures; no live SEC requests should be needed.

1. Recognize `PART I` and `PART II`.
2. Recognize `Item 1`, `Item 1A`, `Item 7`, and `Item 8`.
3. Normalize whitespace and non-breaking spaces in labels.
4. Assign paragraphs to the correct active part/item.
5. Build nested heading paths such as `Products > iPhone`.
6. Do not classify bold text inside ordinary prose as a new heading.
7. Do not classify every italic phrase as a heading.
8. Preserve document order.
9. Avoid duplicate text from nested elements.
10. Exclude scripts/styles while preserving meaningful content.
11. Extract table headers and values.
12. Split large tables into coherent row groups with repeated headers/context.
13. Resolve intra-document `href="#id"` links.
14. Allow multiple link labels to target one anchor without duplicating the destination unit.
15. Handle missing/invalid anchor targets.
16. Distinguish external links from same-document links.
17. Preserve source IDs/anchors when available.
18. Do not invent page numbers when pagination is not reliable.
19. Chunk paragraphs and split oversized paragraphs at sentence boundaries when possible.
20. Never merge chunks across different SEC Items.
21. Keep `content_text` separate from contextual `embedding_text`.
22. Produce deterministic chunk IDs across repeated runs.
23. Handle empty or malformed HTML gracefully.
24. Ensure every chunk has required metadata.
25. Pass the full offline test suite.

Use fixtures representing the generic `div`/`span` markup seen in SEC filings, not only idealized `<h1>`/`<p>` documents. Assert meaningful values and relationships rather than brittle full-HTML strings.

## Definition of done / test pass gate

- [ ] Part/item/heading hierarchy is preserved.
- [ ] SEC Item boundaries are recognized and never crossed by a chunk.
- [ ] Paragraphs and tables retain useful source context.
- [ ] Link aliases and anchor destinations are represented separately.
- [ ] Multiple links to one anchor do not duplicate destination content.
- [ ] Page numbers are only recorded when grounded in reliable pagination.
- [ ] Chunk metadata and deterministic IDs are implemented.
- [ ] All required offline tests are implemented.
- [ ] `python -m pytest -q` passes from the repository root.
- [ ] The standard suite requires no live SEC requests.

## Expected completion report

Report files created/modified, hierarchy detection rules, chunking defaults and size units, table and anchor handling, exact test command and pass/fail/skip summary, limitations, and confirmation that tests ran offline.
