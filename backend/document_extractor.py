"""Extract readable text from SEC HTML/XML documents."""

from __future__ import annotations

import re
from dataclasses import dataclass

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

SKIP_TAGS = {
    "script",
    "style",
    "noscript",
    "template",
    "svg",
    "canvas",
    "iframe",
    "ix:header",
    "ix:hidden",
}
HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK_TAGS = {
    "div",
    "section",
    "article",
    "main",
    "header",
    "footer",
    "ul",
    "ol",
    "dl",
    "dt",
    "dd",
    "pre",
    "blockquote",
    "form",
    "figure",
    "figcaption",
    "nav",
    "aside",
}
MIN_TEXT_LENGTH = 20


class ExtractionError(Exception):
    """Base class for document extraction failures."""


class EmptyExtractionError(ExtractionError):
    """The document produced no usable text."""


@dataclass(frozen=True)
class ExtractionResult:
    text: str
    title: str
    content_type: str
    source_url: str


def _is_xml(content: bytes, content_type: str) -> bool:
    if "xml" in (content_type or "").lower():
        return True
    head = content.lstrip()[:200].lower()
    return (
        head.startswith(b"<?xml")
        or head.startswith(b"<sec-document")
        or head.startswith(b"<xbrl")
    )


def _children(node: Tag) -> str:
    return "".join(_render(child) for child in node.children)


def _render(node) -> str:
    if isinstance(node, (Comment, Declaration, Doctype, ProcessingInstruction)):
        return ""
    if isinstance(node, NavigableString):
        return re.sub(r"\s+", " ", str(node))
    if not isinstance(node, Tag):
        return ""

    name = node.name.lower()
    if name in SKIP_TAGS:
        return ""
    if name == "br":
        return "\n"
    if name in HEADING_TAGS:
        return f"\n\n{_children(node).strip()}\n\n"
    if name == "p":
        return f"\n\n{_children(node).strip()}\n"
    if name == "li":
        return f"\n- {_children(node).strip()}"
    if name == "tr":
        cells = [
            _children(cell).strip()
            for cell in node.find_all(["td", "th"], recursive=False)
        ]
        cells = [cell for cell in cells if cell]
        if not cells:
            return ""
        return "\n" + " | ".join(cells)
    if name in ("td", "th"):
        return " " + _children(node).strip()
    if name in BLOCK_TAGS:
        return f"\n{_children(node)}\n"
    return _children(node)


def _normalize(text: str) -> str:
    text = text.replace("\xa0", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_document(
    content: bytes | str,
    content_type: str = "",
    source_url: str = "",
) -> ExtractionResult:
    """Convert an SEC HTML or XML document into structured plain text."""
    if isinstance(content, str):
        raw = content.encode("utf-8", errors="replace")
    else:
        raw = content

    if not raw or not raw.strip():
        raise EmptyExtractionError(f"Document at {source_url} is empty")

    parser = "lxml-xml" if _is_xml(raw, content_type) else "lxml"
    try:
        soup = BeautifulSoup(raw, parser)
    except Exception as exc:
        raise ExtractionError(
            f"Could not parse document at {source_url}: {exc}"
        ) from exc

    for tag in soup.find_all(list(SKIP_TAGS)):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = re.sub(r"\s+", " ", str(soup.title.string)).strip()
    if not title:
        heading = soup.find("h1")
        if heading:
            title = re.sub(r"\s+", " ", heading.get_text(" ", strip=True)).strip()

    text = _normalize(_children(soup))
    if len(text) < MIN_TEXT_LENGTH:
        raise EmptyExtractionError(
            f"Document at {source_url} produced no usable text"
        )

    resolved_type = content_type or (
        "application/xml" if parser == "lxml-xml" else "text/html"
    )
    return ExtractionResult(
        text=text,
        title=title,
        content_type=resolved_type,
        source_url=source_url,
    )
