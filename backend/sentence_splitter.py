"""Sentence segmentation for SEC prose with abbreviation protection."""

from __future__ import annotations

import re

SENTINEL = "\x00"

ABBREVIATION_RE = re.compile(
    r"\b(?:Item\s+\d{1,3}[A-Za-z]?|Inc|Corp|Co|Ltd|LLC|Nos?|vs|etc|Mr|Mrs|Ms|Dr|St|Ave"
    r"|approx|Dept|Est|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec|Bros|Assn|Sec"
    r"|Art|Fig|Ex|U\.S|U\.K|e\.g|i\.e|a\.m|p\.m)\.(?=\s)",
    re.IGNORECASE,
)
INITIAL_RE = re.compile(r"\b[A-Z]\.(?=\s)")
BOUNDARY_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'\u201c\u2018(\[])")


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def _protect(text: str) -> str:
    text = ABBREVIATION_RE.sub(lambda match: match.group(0)[:-1] + SENTINEL, text)
    return INITIAL_RE.sub(lambda match: match.group(0)[:-1] + SENTINEL, text)


def _is_meaningful(sentence: str) -> bool:
    return bool(re.search(r"[A-Za-z0-9]", sentence)) and len(sentence) >= 2


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences without breaking visual line wraps."""
    if not text:
        return []

    normalized = normalize_text(text)
    if not normalized:
        return []

    protected = _protect(normalized)
    sentences = []
    for part in BOUNDARY_RE.split(protected):
        sentence = part.replace(SENTINEL, ".").strip()
        if _is_meaningful(sentence):
            sentences.append(sentence)
    return sentences
