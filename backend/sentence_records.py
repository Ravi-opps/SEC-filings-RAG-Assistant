"""Build sentence-level, hierarchy-aware records from SEC filing HTML.

This module never downloads a filing and never calls an embedding or vector
service. It parses the DOM in document order, tracks PART / Item / subsection
context, and emits deterministic content units for prose, lists, and tables.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from urllib.parse import unquote

from bs4 import (
    BeautifulSoup,
    CData,
    Comment,
    Declaration,
    Doctype,
    NavigableString,
    ProcessingInstruction,
    Tag,
)

from sentence_splitter import normalize_text, split_sentences

PARSER_VERSION = "1"
EMBEDDING_STRATEGY = "hierarchy-prefix-v1"
DEFAULT_BASE_FONT_SIZE_PX = 16.0 * 10 / 12  # 10pt, the common SEC body size
MAX_TABLE_ROWS = 60
TABLE_GROUP_SIZE = 40

SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "object",
    "embed",
    "input",
    "button",
    "select",
    "textarea",
    "ix:header",
    "ix:hidden",
    "ix:references",
}

HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

BLOCK_TAGS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "aside",
    "nav",
    "form",
    "figure",
    "figcaption",
    "blockquote",
    "pre",
    "center",
    "address",
    "dl",
    "dt",
    "dd",
    "ul",
    "ol",
    "li",
    "table",
    "caption",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "td",
    "th",
}

PART_RE = re.compile(
    r"^PART\s+([IVXLCDM]+)\b(?:\s*[.:\u2014\u2013-]\s*(.*))?$", re.IGNORECASE
)
ITEM_RE = re.compile(
    r"^Item\s+(\d{1,2}[A-Za-z]?)\s*(?:[.:\u2014\u2013-]\s*|\s+)(.+?)\s*\.?$",
    re.IGNORECASE,
)
DOT_LEADER_RE = re.compile(r"(?:\.\s*){5,}|(?:\u00b7\s*){4,}")
PAGE_NUMBER_RE = re.compile(
    r"^(?:page\s*)?[-\u2013\u2014]?\s*\d{1,4}\s*[-\u2013\u2014]?$", re.IGNORECASE
)
INLINE_SENTENCE_RE = re.compile(r"[.!?]\s+[A-Z]")
STYLE_DECL_RE = re.compile(r"([A-Za-z-]+)\s*:\s*([^;]+)")
LENGTH_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(px|pt|em|rem|%)?", re.IGNORECASE)

FONT_SIZE_MAP = {1: 10.0, 2: 13.0, 3: 16.0, 4: 18.0, 5: 24.0, 6: 32.0, 7: 48.0}

PROSE_STARTERS = {
    "of", "to", "in", "on", "for", "and", "or", "is", "are", "was", "were",
    "be", "been", "this", "that", "these", "those", "we", "the", "a", "an",
    "has", "have", "had", "includes", "include", "requires", "require",
    "sets", "set", "provides", "provide", "above", "below", "shall", "may",
    "will",
}


class StructureError(Exception):
    """Base class for structure parsing failures."""


@dataclass(frozen=True)
class FilingMeta:
    document_id: str
    ticker: str
    cik: str
    form_type: str
    accession_number: str
    source_url: str = ""
    company: str | None = None
    filing_date: str | None = None
    report_date: str | None = None


@dataclass
class SentenceRecord:
    point_id: str
    document_id: str
    ticker: str
    cik: str
    form_type: str
    accession_number: str
    part: str | None
    item_id: str | None
    item_title: str | None
    hierarchy: list[str]
    unit_type: str
    text: str
    embedding_text: str
    order_index: int
    sentence_index_in_block: int
    source_dom_id: str | None
    anchor_id: str | None
    year: int | None
    source_url: str
    embedding_model: str
    parser_version: str = PARSER_VERSION
    embedding_strategy: str = EMBEDDING_STRATEGY
    table_index: int | None = None
    table_group_index: int | None = None
    source_type: str | None = None

    def to_payload(self) -> dict:
        """Qdrant payload. The vector is stored in the point vector field."""
        return {
            "point_id": self.point_id,
            "document_id": self.document_id,
            "ticker": self.ticker,
            "cik": self.cik,
            "form_type": self.form_type,
            "accession_number": self.accession_number,
            "part": self.part,
            "item_id": self.item_id,
            "item_title": self.item_title,
            "hierarchy": list(self.hierarchy),
            "unit_type": self.unit_type,
            "text": self.text,
            "embedding_text": self.embedding_text,
            "order_index": self.order_index,
            "sentence_index_in_block": self.sentence_index_in_block,
            "source_dom_id": self.source_dom_id,
            "anchor_id": self.anchor_id,
            "year": self.year,
            "source_url": self.source_url,
            "embedding_model": self.embedding_model,
            "embedding_strategy": self.embedding_strategy,
            "parser_version": self.parser_version,
            "table_index": self.table_index,
            "table_group_index": self.table_group_index,
            "source_type": self.source_type,
        }


@dataclass
class LinkRecord:
    link_id: str
    document_id: str
    source_unit_id: str | None
    link_text: str
    href: str
    target_anchor: str | None
    target_unit_id: str | None
    external: bool
    resolved: bool
    link_index: int

    def to_payload(self) -> dict:
        return {
            "link_id": self.link_id,
            "document_id": self.document_id,
            "source_unit_id": self.source_unit_id,
            "link_text": self.link_text,
            "href": self.href,
            "target_anchor": self.target_anchor,
            "target_unit_id": self.target_unit_id,
            "external": self.external,
            "resolved": self.resolved,
            "link_index": self.link_index,
        }


@dataclass
class ParsedFiling:
    meta: FilingMeta
    title: str
    records: list[SentenceRecord]
    links: list[LinkRecord]
    anchor_targets: dict[str, str]
    parser_version: str = PARSER_VERSION

    @property
    def unit_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for record in self.records:
            counts[record.unit_type] = counts.get(record.unit_type, 0) + 1
        return counts


@dataclass
class _Style:
    bold: bool = False
    italic: bool = False
    font_size_px: float | None = None
    indent_px: float | None = None
    align: str | None = None


@dataclass
class _BlockMetrics:
    text: str
    bold_ratio: float
    italic_ratio: float
    font_size_px: float | None
    word_count: int


@dataclass
class _HeadingDecision:
    is_heading: bool
    confidence: float = 0.0
    reason: str = ""


@dataclass
class _State:
    meta: FilingMeta
    embedding_model: str
    include_heading_records: bool
    base_font_size: float
    parser_version: str = PARSER_VERSION
    part: str | None = None
    item_id: str | None = None
    item_title: str | None = None
    headings: list[tuple[int, str]] = field(default_factory=list)
    records: list[SentenceRecord] = field(default_factory=list)
    links: list[LinkRecord] = field(default_factory=list)
    anchor_targets: dict[str, str] = field(default_factory=dict)
    pending_anchors: list[str] = field(default_factory=list)
    order_index: int = 0
    block_index: int = 0
    table_index: int = 0
    link_index: int = 0

    def min_heading_level(self) -> int:
        if self.item_id:
            return 2
        if self.part:
            return 1
        return 0

    def heading_offset(self, style: _Style) -> int:
        if style.font_size_px and self.base_font_size > 0:
            ratio = style.font_size_px / self.base_font_size
            if ratio >= 1.35:
                return 0
            if ratio >= 1.12:
                return 1
            if ratio >= 0.95:
                return 2
            return 3
        return 2

    def reset_for_part(self) -> None:
        self.item_id = None
        self.item_title = None
        self.headings = []

    def push_heading(self, level: int, text: str) -> None:
        while self.headings and self.headings[-1][0] >= level:
            self.headings.pop()
        self.headings.append((level, text))

    def bind_anchor(self, anchor_id: str) -> None:
        if anchor_id and anchor_id not in self.pending_anchors:
            self.pending_anchors.append(anchor_id)


def build_embedding_text(hierarchy: list[str], text: str) -> str:
    if not hierarchy:
        return text
    return " > ".join(hierarchy) + "\n\n" + text


def filing_year(meta: FilingMeta) -> int | None:
    """Return the report/fiscal year, falling back to the filing year."""
    for value in (meta.report_date, meta.filing_date):
        if not value:
            continue
        head = str(value)[:4]
        if head.isdigit():
            return int(head)
    return None


def make_point_id(
    accession_number: str,
    document_id: str,
    dom_key: str,
    sentence_index: int,
    text: str,
) -> str:
    raw = "|".join(
        [accession_number, document_id, dom_key, str(sentence_index), text]
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _parse_style(style_attr: str | None) -> dict[str, str]:
    if not style_attr:
        return {}
    declarations: dict[str, str] = {}
    for part in style_attr.split(";"):
        match = STYLE_DECL_RE.match(part.strip())
        if match:
            declarations[match.group(1).strip().lower()] = match.group(2).strip()
    return declarations


def _length_to_px(value: str, base: float | None = None) -> float | None:
    match = LENGTH_RE.search(value)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    if unit == "pt":
        return amount * 4.0 / 3.0
    if unit in {"em", "rem"}:
        return amount * 16.0
    if unit == "%":
        return amount * (base if base else 16.0) / 100.0
    return amount


def _font_size_from_element(element: Tag) -> float | None:
    style = _parse_style(element.get("style"))
    value = style.get("font-size")
    if value:
        size = _length_to_px(value)
        if size:
            return size
    size_attr = element.get("size")
    if size_attr and size_attr.strip().isdigit():
        return FONT_SIZE_MAP.get(int(size_attr.strip()))
    return None


def _class_tokens(element: Tag) -> set[str]:
    classes = element.get("class") or []
    return {str(item).lower() for item in classes}


def _effective_style(element: Tag) -> _Style:
    bold = False
    italic = False
    font_size: float | None = None
    indent: float | None = None
    align: str | None = None
    current: Tag | None = element
    while current is not None:
        name = (current.name or "").lower()
        classes = _class_tokens(current)
        if name in {"b", "strong"} or "bold" in classes:
            bold = True
        if name in {"i", "em"} or "italic" in classes:
            italic = True
        declarations = _parse_style(current.get("style"))
        weight = declarations.get("font-weight", "").lower().strip()
        if weight in {"bold", "bolder", "600", "700", "800", "900"}:
            bold = True
        font_style = declarations.get("font-style", "").lower().strip()
        if font_style in {"italic", "oblique"}:
            italic = True
        if font_size is None and "font-size" in declarations:
            font_size = _length_to_px(declarations["font-size"])
        if indent is None:
            for key in ("margin-left", "padding-left", "text-indent"):
                if key in declarations:
                    indent = _length_to_px(declarations[key])
                    break
        if align is None and "text-align" in declarations:
            align = declarations["text-align"].strip().lower()
        if font_size is None:
            font_size = _font_size_from_element(current)
        current = current.parent if isinstance(current.parent, Tag) else None
    return _Style(
        bold=bold,
        italic=italic,
        font_size_px=font_size,
        indent_px=indent,
        align=align,
    )


def _is_hidden(element: Tag) -> bool:
    if element.has_attr("hidden"):
        return True
    if str(element.get("aria-hidden", "")).lower() == "true":
        return True
    declarations = _parse_style(element.get("style"))
    if declarations.get("display", "").lower().strip() == "none":
        return True
    visibility = declarations.get("visibility", "").lower().strip()
    return visibility in {"hidden", "collapse"}


def _join_text_parts(parts: list[str]) -> str:
    out = ""
    for part in parts:
        if not part:
            continue
        if out and not out.endswith(
            (
                " ",
                "\n",
                "(",
                "[",
                "{",
                "/",
                "-",
                "\u2014",
                "\u2013",
                "'",
                "\u2019",
                '"',
                "\u201c",
            )
        ):
            if not part.startswith(
                (
                    " ",
                    "\n",
                    ".",
                    ",",
                    ";",
                    ":",
                    "!",
                    "?",
                    ")",
                    "]",
                    "}",
                    "%",
                    "'",
                    "\u2019",
                    '"',
                    "\u201d",
                )
            ):
                out += " "
        out += part
    return normalize_text(out)


def _text_of(element: Tag) -> str:
    parts = [
        str(node)
        for node in element.find_all(string=True)
        if _is_text_node(node)
    ]
    return _join_text_parts(parts)


def _word_count(text: str) -> int:
    return len(text.split())


def _is_noise_text(text: str) -> bool:
    clean = normalize_text(text)
    if not clean:
        return True
    if PAGE_NUMBER_RE.match(clean):
        return True
    if DOT_LEADER_RE.search(clean):
        return True
    return clean.lower() in {"table of contents", "contents"}


def _clean_cell(text: str) -> str:
    return normalize_text(text).replace("|", r"\|")


def _table_markdown(headers: list[str], rows: list[list[str]]) -> str:
    width = max([len(headers)] + [len(row) for row in rows]) if (headers or rows) else 0
    if width == 0:
        return ""
    normalized_headers = [headers[i] if i < len(headers) else "" for i in range(width)]
    if not any(normalized_headers):
        normalized_headers = [f"Column {i + 1}" for i in range(width)]
    lines = ["| " + " | ".join(normalized_headers) + " |"]
    lines.append("| " + " | ".join(["---"] * width) + " |")
    for row in rows:
        cells = [row[i] if i < len(row) else "" for i in range(width)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _parse_part(text: str) -> tuple[str, str | None] | None:
    match = PART_RE.match(normalize_text(text))
    if not match:
        return None
    part = f"PART {match.group(1).upper()}"
    title = normalize_text(match.group(2) or "")
    if title:
        first = title.split()[0]
        if first[:1].islower() and first.lower() in PROSE_STARTERS:
            return None
        if INLINE_SENTENCE_RE.search(title) or DOT_LEADER_RE.search(title):
            return None
    return part, title or None


def _parse_item(text: str) -> tuple[str, str] | None:
    clean = normalize_text(text)
    match = ITEM_RE.match(clean)
    if not match:
        return None
    item_id = match.group(1).upper()
    title = normalize_text(match.group(2)).rstrip(".")
    if not title or len(title) > 200:
        return None
    first = re.split(r"\s+", title)[0].lower()
    if first in PROSE_STARTERS:
        return None
    if INLINE_SENTENCE_RE.search(title):
        return None
    if _word_count(title) > 18:
        return None
    if DOT_LEADER_RE.search(title):
        return None
    return item_id, title


def _looks_like_heading(
    metrics: _BlockMetrics,
    style: _Style,
    item_active: bool,
    base_font_size: float,
) -> _HeadingDecision:
    text = metrics.text
    if not text or len(text) > 200:
        return _HeadingDecision(False)
    if DOT_LEADER_RE.search(text):
        return _HeadingDecision(False)
    if text[-1] in ".!?":
        return _HeadingDecision(False)
    if metrics.word_count > 24:
        return _HeadingDecision(False)
    if INLINE_SENTENCE_RE.search(text):
        return _HeadingDecision(False)

    size = metrics.font_size_px or style.font_size_px
    score = 0.0
    reasons: list[str] = []
    if metrics.bold_ratio >= 0.85:
        score += 0.6
        reasons.append("bold")
    if (
        metrics.italic_ratio >= 0.85
        and metrics.bold_ratio < 0.85
        and metrics.word_count <= 10
        and item_active
    ):
        score += 0.35
        reasons.append("italic-label")
    if size and base_font_size > 0 and size >= 1.12 * base_font_size:
        score += 0.3
        reasons.append("font-size")
    if metrics.bold_ratio >= 0.99:
        score += 0.1
        reasons.append("fully-bold")

    return _HeadingDecision(score >= 0.55, min(score, 1.0), "+".join(reasons))


def _collect_nodes(element: Tag) -> list[NavigableString]:
    nodes: list[NavigableString] = []
    for node in element.find_all(string=True):
        if isinstance(
            node,
            (Comment, Declaration, Doctype, ProcessingInstruction, CData),
        ):
            continue
        if not isinstance(node, NavigableString):
            continue
        skipped = False
        parent = node.parent
        while parent is not None and parent is not element:
            if isinstance(parent, Tag):
                if _is_hidden(parent) or parent.name in SKIP_TAGS:
                    skipped = True
                    break
                if parent.name in {"ul", "ol", "table"}:
                    skipped = True
                    break
            parent = parent.parent
        if not skipped:
            nodes.append(node)
    return nodes


def _metrics_from_nodes(nodes: list[NavigableString]) -> _BlockMetrics:
    texts: list[str] = []
    total = 0
    bold_chars = 0
    italic_chars = 0
    sizes: list[float] = []
    for node in nodes:
        raw = str(node)
        value = normalize_text(raw)
        if not value:
            continue
        texts.append(raw)
        style = _effective_style(node.parent)
        compact = re.sub(r"\s+", "", value)
        count = len(compact)
        total += count
        if style.bold:
            bold_chars += count
        if style.italic:
            italic_chars += count
        if style.font_size_px:
            sizes.append(style.font_size_px)
    text = _join_text_parts(texts)
    return _BlockMetrics(
        text=text,
        bold_ratio=bold_chars / total if total else 0.0,
        italic_ratio=italic_chars / total if total else 0.0,
        font_size_px=max(sizes) if sizes else None,
        word_count=_word_count(text),
    )


def _estimate_base_font_size(soup: BeautifulSoup) -> float:
    counts: dict[float, int] = {}
    for node in soup.find_all(string=True):
        if not isinstance(node, NavigableString) or isinstance(
            node, (Comment, Declaration, Doctype, ProcessingInstruction, CData)
        ):
            continue
        if len(normalize_text(str(node))) < 60 or node.parent is None:
            continue
        style = _effective_style(node.parent)
        if style.font_size_px:
            key = round(style.font_size_px, 1)
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return DEFAULT_BASE_FONT_SIZE_PX
    return max(counts, key=lambda key: counts[key])


def _is_toc_like(element: Tag) -> bool:
    identity = " ".join(
        [str(element.get("id") or ""), *sorted(_class_tokens(element))]
    ).lower()
    if "table-of-contents" in identity:
        return True
    if re.search(r"(^|[\s_-])toc($|[\s_-])", identity):
        return True
    anchors = element.find_all("a", href=True)
    intra = [a for a in anchors if str(a.get("href") or "").startswith("#")]
    if len(intra) >= 3:
        leaders = DOT_LEADER_RE.findall(_text_of(element))
        if len(leaders) >= 2:
            return True
    return False


def _contains_block(element: Tag) -> bool:
    for child in element.find_all(True):
        if child.name in BLOCK_TAGS or child.name in HEADING_TAGS:
            return True
    return False


def _make_record(
    state: _State,
    *,
    unit_type: str,
    text: str,
    element: Tag | None,
    sentence_index: int = 0,
    dom_key: str | None = None,
    table_index: int | None = None,
    table_group_index: int | None = None,
) -> SentenceRecord:
    hierarchy = [entry[1] for entry in state.headings if entry[1]]
    dom_id = element.get("id") if element is not None else None
    key = dom_key or dom_id or f"block-{state.block_index}"
    point_id = make_point_id(
        state.meta.accession_number,
        state.meta.document_id,
        key,
        sentence_index,
        text,
    )
    anchor = None
    if state.pending_anchors:
        for pending in state.pending_anchors:
            state.anchor_targets.setdefault(pending, point_id)
        anchor = state.pending_anchors[0]
        state.pending_anchors.clear()
    if dom_id:
        state.anchor_targets.setdefault(dom_id, point_id)
    record = SentenceRecord(
        point_id=point_id,
        document_id=state.meta.document_id,
        ticker=state.meta.ticker,
        cik=state.meta.cik,
        form_type=state.meta.form_type,
        accession_number=state.meta.accession_number,
        part=state.part,
        item_id=state.item_id,
        item_title=state.item_title,
        hierarchy=hierarchy,
        unit_type=unit_type,
        text=text,
        embedding_text=build_embedding_text(hierarchy, text),
        order_index=state.order_index,
        sentence_index_in_block=sentence_index,
        source_dom_id=dom_id,
        anchor_id=dom_id or anchor,
        year=filing_year(state.meta),
        source_url=state.meta.source_url,
        embedding_model=state.embedding_model,
        parser_version=state.parser_version,
        table_index=table_index,
        table_group_index=table_group_index,
    )
    state.records.append(record)
    state.order_index += 1
    return record


def _links_from(
    element: Tag, state: _State, source_unit_id: str | None
) -> None:
    anchors: list[Tag] = []
    if element.name == "a" and element.get("href"):
        anchors.append(element)
    else:
        parent = element.find_parent("a", href=True)
        if parent is not None:
            anchors.append(parent)
    anchors.extend(element.find_all("a", href=True))
    seen: set[tuple[str, str]] = set()
    for anchor in anchors:
        href = str(anchor.get("href") or "").strip()
        if not href:
            continue
        link_text = _text_of(anchor)
        signature = (href, link_text)
        if signature in seen:
            continue
        seen.add(signature)
        external = not href.startswith("#")
        target_anchor = None if external else (unquote(href[1:]) or None)
        record = LinkRecord(
            link_id=hashlib.sha256(
                "|".join(
                    [
                        state.meta.document_id,
                        source_unit_id or "",
                        href,
                        link_text,
                        str(state.link_index),
                    ]
                ).encode("utf-8")
            ).hexdigest()[:32],
            document_id=state.meta.document_id,
            source_unit_id=source_unit_id,
            link_text=link_text,
            href=href,
            target_anchor=target_anchor,
            target_unit_id=None,
            external=external,
            resolved=False,
            link_index=state.link_index,
        )
        state.links.append(record)
        state.link_index += 1


def _emit_text(
    state: _State, element: Tag, text: str, unit_type: str = "paragraph"
) -> None:
    clean = normalize_text(text)
    if not clean or _is_noise_text(clean):
        return
    sentences = split_sentences(clean)
    if not sentences:
        return
    first_id: str | None = None
    for index, sentence in enumerate(sentences):
        if _is_noise_text(sentence):
            continue
        record = _make_record(
            state,
            unit_type=unit_type,
            text=sentence,
            element=element,
            sentence_index=index,
        )
        if first_id is None:
            first_id = record.point_id
    if first_id is not None:
        _links_from(element, state, first_id)


def _emit_heading_record(
    state: _State, element: Tag, text: str
) -> None:
    if not state.include_heading_records:
        return
    clean = normalize_text(text)
    if not clean:
        return
    _make_record(state, unit_type="heading", text=clean, element=element)


def _apply_part(state: _State, text: str) -> bool:
    parsed = _parse_part(text)
    if parsed is None:
        return False
    part, _title = parsed
    state.part = part
    state.reset_for_part()
    state.headings = [(0, part)]
    return True


def _apply_item(state: _State, text: str) -> bool:
    parsed = _parse_item(text)
    if parsed is None:
        return False
    item_id, item_title = parsed
    state.item_id = item_id
    state.item_title = item_title
    state.headings = [entry for entry in state.headings if entry[0] < 1]
    state.push_heading(1, f"Item {item_id} \u2014 {item_title}")
    return True


def _handle_heading(state: _State, element: Tag, tag_level: int) -> None:
    text = _text_of(element)
    if not text:
        return
    if _apply_part(state, text):
        _emit_heading_record(state, element, text)
        state.block_index += 1
        return
    if _apply_item(state, text):
        _emit_heading_record(state, element, text)
        state.block_index += 1
        return
    if _is_noise_text(text) or _is_toc_like(element):
        state.block_index += 1
        return
    level = state.min_heading_level() + (tag_level - 1)
    state.push_heading(level, text)
    _emit_heading_record(state, element, text)
    state.block_index += 1


def _handle_block_text(state: _State, element: Tag) -> None:
    nodes = _collect_nodes(element)
    if not nodes:
        state.block_index += 1
        return
    metrics = _metrics_from_nodes(nodes)
    text = metrics.text
    if not text:
        state.block_index += 1
        return
    if _apply_part(state, text):
        _emit_heading_record(state, element, text)
        state.block_index += 1
        return
    if _apply_item(state, text):
        _emit_heading_record(state, element, text)
        state.block_index += 1
        return
    if _is_noise_text(text):
        state.block_index += 1
        return
    style = _effective_style(nodes[0].parent)
    if metrics.font_size_px and (
        not style.font_size_px or metrics.font_size_px > style.font_size_px
    ):
        style.font_size_px = metrics.font_size_px
    decision = _looks_like_heading(
        metrics, style, state.item_id is not None, state.base_font_size
    )
    if decision.is_heading and not _contains_block(element):
        level = state.min_heading_level() + state.heading_offset(style)
        state.push_heading(level, text)
        _emit_heading_record(state, element, text)
        state.block_index += 1
        return
    _emit_text(state, element, text)
    state.block_index += 1


def _cell_text(cell: Tag) -> str:
    parts: list[str] = []
    for node in cell.find_all(string=True):
        if not isinstance(node, NavigableString) or isinstance(
            node, (Comment, Declaration, Doctype, ProcessingInstruction, CData)
        ):
            continue
        parent = node.parent
        nested = False
        while parent is not None and parent is not cell:
            if isinstance(parent, Tag) and parent.name == "table":
                nested = True
                break
            parent = parent.parent
        if not nested:
            parts.append(str(node))
    return _clean_cell(_join_text_parts(parts))


def _row_cells(row: Tag) -> list[Tag]:
    return row.find_all(["td", "th"], recursive=False)


def _handle_table(state: _State, element: Tag) -> None:
    if _is_toc_like(element):
        state.block_index += 1
        return
    all_rows = element.find_all("tr")
    rows = [row for row in all_rows if row.find_parent("table") is element]
    if not rows:
        state.block_index += 1
        return
    measured = [(row, _row_cells(row)) for row in rows]
    layout = False
    if element.find("table") is not None:
        layout = True
    if any(len(cells) <= 1 for _row, cells in measured):
        layout = True
    for _row, cells in measured:
        if any(len(_text_of(cell)) > 4000 for cell in cells):
            layout = True
            break
    if layout:
        _walk_container(element, state)
        return

    caption_tag = element.find("caption")
    caption = _text_of(caption_tag) if caption_tag is not None else ""
    header_cells: list[str] = []
    body_rows: list[list[str]] = []
    thead = element.find("thead")
    header_row: Tag | None = None
    if thead is not None:
        thead_rows = [
            row for row in thead.find_all("tr") if row.find_parent("table") is element
        ]
        if thead_rows:
            header_row = thead_rows[0]
    if header_row is None and measured:
        first_row, first_cells = measured[0]
        if first_cells and all(cell.name == "th" for cell in first_cells):
            header_row = first_row
    if header_row is not None:
        header_cells = [_cell_text(cell) for cell in _row_cells(header_row)]
    for row, cells in measured:
        if row is header_row:
            continue
        if not cells:
            continue
        body_rows.append([_cell_text(cell) for cell in cells])
    if not body_rows and not header_cells:
        state.block_index += 1
        return

    state.table_index += 1
    table_number = state.table_index
    if len(body_rows) > MAX_TABLE_ROWS:
        groups = [
            body_rows[start : start + TABLE_GROUP_SIZE]
            for start in range(0, len(body_rows), TABLE_GROUP_SIZE)
        ]
    else:
        groups = [body_rows]
    base_key = element.get("id") or f"block-{state.block_index}"
    for group_index, group in enumerate(groups):
        markdown = _table_markdown(header_cells, group)
        text = f"{caption}\n\n{markdown}" if caption else markdown
        dom_key = base_key if group_index == 0 else f"{base_key}#g{group_index}"
        record = _make_record(
            state,
            unit_type="table",
            text=text,
            element=element,
            dom_key=dom_key,
            table_index=table_number,
            table_group_index=group_index,
        )
        if group_index == 0:
            _links_from(element, state, record.point_id)
    state.block_index += 1


def _walk_list_item(state: _State, item: Tag) -> None:
    _bind_anchors(state, item)
    buffer: list[NavigableString | str] = []
    for child in item.children:
        if isinstance(child, Tag):
            if child.name in SKIP_TAGS or _is_hidden(child):
                continue
            if child.name == "br":
                buffer.append(" ")
                continue
            if child.name in BLOCK_TAGS or child.name in HEADING_TAGS:
                _flush_list_buffer(state, item, buffer)
                _walk_element(child, state)
            else:
                _bind_anchors(state, child)
                buffer.extend(_collect_nodes(child))
        elif _is_text_node(child):
            buffer.append(child)
    _flush_list_buffer(state, item, buffer)


def _flush_list_buffer(
    state: _State, item: Tag, buffer: list[NavigableString | str]
) -> None:
    nodes = [node for node in buffer if isinstance(node, NavigableString)]
    buffer.clear()
    if not nodes:
        return
    metrics = _metrics_from_nodes(nodes)
    if not metrics.text or _is_noise_text(metrics.text):
        return
    if _apply_part(state, metrics.text):
        _emit_heading_record(state, item, metrics.text)
        state.block_index += 1
        return
    if _apply_item(state, metrics.text):
        _emit_heading_record(state, item, metrics.text)
        state.block_index += 1
        return
    _emit_text(state, item, metrics.text, unit_type="list_item")
    state.block_index += 1


def _handle_list(state: _State, element: Tag) -> None:
    items = element.find_all("li", recursive=False)
    if not items:
        _walk_container(element, state)
        state.block_index += 1
        return
    for item in items:
        _walk_list_item(state, item)


def _is_text_node(value: object) -> bool:
    if not isinstance(value, NavigableString):
        return False
    return not isinstance(
        value, (Comment, Declaration, Doctype, ProcessingInstruction, CData)
    )


def _anchor_ids(element: Tag) -> list[str]:
    anchors: list[str] = []
    element_id = element.get("id")
    if element_id:
        anchors.append(str(element_id))
    if element.name == "a":
        name = element.get("name")
        if name:
            anchors.append(str(name))
    return anchors


def _bind_anchors(state: _State, element: Tag) -> None:
    for anchor_id in _anchor_ids(element):
        state.bind_anchor(anchor_id)


def _walk_element(element: Tag, state: _State) -> None:
    if not isinstance(element, Tag):
        return
    if element.name in SKIP_TAGS or _is_hidden(element):
        return
    if _is_toc_like(element):
        state.block_index += 1
        return
    _bind_anchors(state, element)
    if element.name in HEADING_TAGS:
        _handle_heading(state, element, int(element.name[1]))
        return
    if element.name == "table":
        _handle_table(state, element)
        return
    if element.name in {"ul", "ol"}:
        _handle_list(state, element)
        return
    if element.name == "li":
        _walk_list_item(state, element)
        return
    if element.name in {"p", "pre", "blockquote", "dt", "dd", "figcaption"}:
        _handle_block_text(state, element)
        return
    _walk_container(element, state)


def _walk_container(element: Tag, state: _State) -> None:
    buffer: list[NavigableString | str] = []
    for child in element.children:
        if isinstance(child, Tag):
            if child.name in SKIP_TAGS or _is_hidden(child):
                continue
            if child.name == "br":
                buffer.append(" ")
                continue
            if child.name == "hr":
                _flush_buffer(state, element, buffer)
                continue
            if child.name in BLOCK_TAGS or child.name in HEADING_TAGS:
                _flush_buffer(state, element, buffer)
                _walk_element(child, state)
            else:
                _bind_anchors(state, child)
                buffer.extend(_collect_nodes(child))
        elif _is_text_node(child):
            buffer.append(child)
    _flush_buffer(state, element, buffer)


def _flush_buffer(
    state: _State, element: Tag, buffer: list[NavigableString | str]
) -> None:
    nodes = [node for node in buffer if isinstance(node, NavigableString)]
    buffer.clear()
    if not nodes:
        return
    metrics = _metrics_from_nodes(nodes)
    if not metrics.text or _is_noise_text(metrics.text):
        return
    cleaned = metrics.text
    if _apply_part(state, cleaned):
        _emit_heading_record(state, element, cleaned)
        state.block_index += 1
        return
    if _apply_item(state, cleaned):
        _emit_heading_record(state, element, cleaned)
        state.block_index += 1
        return
    style = _effective_style(nodes[0].parent)
    if metrics.font_size_px and (
        not style.font_size_px or metrics.font_size_px > style.font_size_px
    ):
        style.font_size_px = metrics.font_size_px
    decision = _looks_like_heading(
        metrics, style, state.item_id is not None, state.base_font_size
    )
    if decision.is_heading:
        level = state.min_heading_level() + state.heading_offset(style)
        state.push_heading(level, cleaned)
        _emit_heading_record(state, element, cleaned)
        state.block_index += 1
        return
    _emit_text(state, element, cleaned)
    state.block_index += 1


def parse_filing_html(
    html: str | bytes | None,
    meta: FilingMeta,
    *,
    embedding_model: str = "",
    parser_version: str = PARSER_VERSION,
    include_heading_records: bool = False,
) -> ParsedFiling:
    """Parse filing HTML into deterministic sentence-level records."""
    if html is None:
        html = ""
    if isinstance(html, bytes):
        html = html.decode("utf-8", errors="replace")
    if not html.strip():
        return ParsedFiling(meta=meta, title="", records=[], links=[], anchor_targets={})
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    title_tag = soup.find("title")
    title = _text_of(title_tag) if title_tag is not None else ""

    head = soup.find("head")
    if head is not None:
        head.decompose()
    for tag in soup.find_all(list(SKIP_TAGS)):
        try:
            tag.decompose()
        except Exception:
            pass
    for tag in soup.find_all(True):
        if tag.parent is None and tag is not soup:
            continue
        if _is_hidden(tag):
            try:
                tag.decompose()
            except Exception:
                pass

    base_font = _estimate_base_font_size(soup)
    state = _State(
        meta=meta,
        embedding_model=embedding_model,
        include_heading_records=include_heading_records,
        base_font_size=base_font,
        parser_version=parser_version,
    )
    body = soup.body if soup.body is not None else soup
    _walk_container(body, state)

    for link in state.links:
        if link.external or not link.target_anchor:
            continue
        target = state.anchor_targets.get(link.target_anchor)
        if target:
            link.target_unit_id = target
            link.resolved = True

    return ParsedFiling(
        meta=meta,
        title=title,
        records=state.records,
        links=state.links,
        anchor_targets=dict(state.anchor_targets),
        parser_version=parser_version,
    )


