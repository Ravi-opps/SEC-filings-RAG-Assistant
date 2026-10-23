"""Qdrant retrieval, reranking, and context formatting for the chat agent."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass
from typing import Any, Sequence
from urllib.parse import quote

import httpx

from embeddings import (
    AsyncEmbeddingClient,
    EmbeddingConfigError,
    EmbeddingError,
)
from qdrant_store import AsyncQdrantStore

DEFAULT_TOP_K = 20
DEFAULT_FINAL_K = 5


class RetrievalError(Exception):
    """Raised when retrieval or reranking fails."""


class RetrievalConfigError(RetrievalError):
    """Raised when retrieval dependencies are not configured."""


@dataclass(frozen=True)
class RetrievedChunk:
    point_id: str
    text: str
    vector_score: float
    rerank_score: float
    payload: dict[str, Any]


def _env(*names: str) -> str:
    for name in names:
        value = os.getenv(name) or os.getenv(name.lower())
        if value:
            return value
    return ""


class RerankerClient:
    """HTTP reranker client for Cohere-style rerank APIs."""

    def __init__(
        self,
        *,
        url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float = 120.0,
        max_attempts: int = 2,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.url = (
            url
            if url is not None
            else _env("RERANK_URL", "RERANKER_URL")
        )
        self.api_key = (
            api_key
            if api_key is not None
            else _env("RERANK_KEY", "RERANKER_KEY")
        )
        self.model = (
            model
            if model is not None
            else _env("RERANK_MODEL", "RERANKER_MODEL")
        )
        self.timeout = timeout
        self.max_attempts = max(1, max_attempts)
        self._client = client
        self._owns_client = client is None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    def ensure_configured(self) -> None:
        if not self.url:
            raise RetrievalConfigError("RERANK_URL is not configured")
        if not self.api_key:
            raise RetrievalConfigError("RERANK_KEY is not configured")
        if not self.model:
            raise RetrievalConfigError("RERANK_MODEL is not configured")

    async def rerank(
        self,
        query: str,
        documents: Sequence[str],
        *,
        top_n: int = DEFAULT_FINAL_K,
    ) -> list[tuple[int, float]]:
        self.ensure_configured()
        if not query.strip():
            raise RetrievalError("reranker query cannot be empty")
        if not documents:
            return []

        limit = max(1, min(top_n, len(documents)))
        body = {
            "model": self.model,
            "query": query,
            "documents": list(documents),
            "top_n": limit,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        retryable_statuses = {429, 500, 502, 503, 504}
        response: httpx.Response | None = None
        for attempt in range(self.max_attempts):
            try:
                response = await self.client.post(
                    self.url,
                    headers=headers,
                    json=body,
                )
            except httpx.TimeoutException as exc:
                if attempt + 1 < self.max_attempts:
                    await asyncio.sleep(1.0)
                    continue
                raise RetrievalError(
                    "reranker request timed out after "
                    f"{self.timeout:.0f}s across {self.max_attempts} attempts"
                ) from exc
            except httpx.HTTPError as exc:
                raise RetrievalError(
                    "reranker request failed: "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
            if (
                response.status_code in retryable_statuses
                and attempt + 1 < self.max_attempts
            ):
                await asyncio.sleep(1.0)
                continue
            break
        if response is None:
            raise RetrievalError("reranker request did not return a response")
        if response.status_code >= 400:
            raise RetrievalError(
                f"reranker request failed with status {response.status_code}"
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RetrievalError("reranker response was not JSON") from exc
        return _parse_rerank(payload, len(documents), limit)

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None


def _parse_rerank(
    payload: Any,
    document_count: int,
    top_n: int,
) -> list[tuple[int, float]]:
    if not isinstance(payload, dict):
        raise RetrievalError("reranker response was not a JSON object")

    items: Any = None
    for key in ("results", "data", "rankings"):
        value = payload.get(key)
        if isinstance(value, list):
            items = value
            break
    if items is None:
        raise RetrievalError("reranker response did not contain results")

    ranked: list[tuple[int, float]] = []
    seen: set[int] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        index = item.get("index", item.get("document_index"))
        try:
            index = int(index)
        except (TypeError, ValueError):
            continue
        if index < 0 or index >= document_count or index in seen:
            continue
        score = None
        for key in ("relevance_score", "rerank_score", "score", "logit"):
            value = item.get(key)
            if isinstance(value, (int, float)):
                score = float(value)
                break
        if score is None:
            continue
        seen.add(index)
        ranked.append((index, score))

    if not ranked:
        raise RetrievalError("reranker response did not contain usable scores")
    ranked.sort(key=lambda pair: pair[1], reverse=True)
    return ranked[:top_n]


class RetrievalService:
    """Embed a query, search Qdrant, rerank, and format the top chunks."""

    def __init__(
        self,
        *,
        store: AsyncQdrantStore | None = None,
        embedder: AsyncEmbeddingClient | None = None,
        reranker: RerankerClient | None = None,
        top_k: int = DEFAULT_TOP_K,
        final_k: int = DEFAULT_FINAL_K,
    ) -> None:
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        if final_k < 1:
            raise ValueError("final_k must be at least 1")
        self._store = store
        self._embedder = embedder
        self._reranker = reranker
        self.top_k = top_k
        self.final_k = final_k

    @property
    def store(self) -> AsyncQdrantStore:
        if self._store is None:
            if not _env("QDRANT_URL"):
                raise RetrievalConfigError("QDRANT_URL is not configured")
            self._store = AsyncQdrantStore()
        return self._store

    @property
    def embedder(self) -> AsyncEmbeddingClient:
        if self._embedder is None:
            self._embedder = AsyncEmbeddingClient()
        return self._embedder

    @property
    def reranker(self) -> RerankerClient:
        if self._reranker is None:
            self._reranker = RerankerClient()
        return self._reranker

    async def retrieve(self, query: str) -> list[RetrievedChunk]:
        clean_query = query.strip()
        if not clean_query:
            raise RetrievalError("retrieval query cannot be empty")
        ensure_configured = getattr(
            self.reranker,
            "ensure_configured",
            None,
        )
        if ensure_configured is not None:
            ensure_configured()

        try:
            vector = await self.embedder.embed_query(clean_query)
            hits = await self.store.search(vector, limit=self.top_k)
        except RetrievalError:
            raise
        except EmbeddingConfigError as exc:
            raise RetrievalConfigError(str(exc)) from exc
        except EmbeddingError as exc:
            raise RetrievalError(f"query embedding failed: {exc}") from exc
        except Exception as exc:
            raise RetrievalError(f"Qdrant search failed: {exc}") from exc

        candidates: list[tuple[int, str]] = []
        for index, hit in enumerate(hits):
            text = str(
                hit.payload.get("embedding_text")
                or hit.payload.get("text")
                or ""
            ).strip()
            if text:
                candidates.append((index, text))
        if not candidates:
            return []

        try:
            ranked = await self.reranker.rerank(
                clean_query,
                [text for _, text in candidates],
                top_n=self.final_k,
            )
        except RetrievalError:
            raise
        except Exception as exc:
            raise RetrievalError(f"reranking failed: {exc}") from exc

        chunks: list[RetrievedChunk] = []
        for candidate_index, score in ranked:
            if candidate_index < 0 or candidate_index >= len(candidates):
                continue
            hit_index, _ = candidates[candidate_index]
            hit = hits[hit_index]
            payload = dict(hit.payload)
            text = str(payload.get("text") or "").strip()
            if not text:
                text = str(payload.get("embedding_text") or "").strip()
            chunks.append(
                RetrievedChunk(
                    point_id=hit.point_id,
                    text=text,
                    vector_score=hit.score,
                    rerank_score=score,
                    payload=payload,
                )
            )
        if not chunks:
            raise RetrievalError("reranker returned no usable chunks")
        return chunks

    async def close(self) -> None:
        for resource in (self._reranker, self._embedder, self._store):
            close = getattr(resource, "close", None)
            if close is not None:
                await close()


def _source_title(payload: dict[str, Any], section: list[str]) -> str:
    ticker = str(payload.get("ticker") or "").strip()
    form_type = str(payload.get("form_type") or "").strip()
    item_id = str(payload.get("item_id") or "").strip()
    item_title = str(payload.get("item_title") or "").strip()
    year = payload.get("year")

    base = " ".join(value for value in (ticker, form_type) if value)
    if not base:
        base = "SEC filing"
    if year:
        base += f" ({year})"
    if item_id:
        base += f" — Item {item_id}"
        if item_title:
            base += f": {item_title}"
    elif section:
        base += f" — {section[-1]}"
    return base


def _table_fragment(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in lines:
        if "total revenue" in line.lower():
            values = re.findall(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", line)
            if values:
                return values[0]
    for line in lines:
        values = re.findall(
            r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+",
            line,
        )
        if values:
            return values[0]
    return ""


def _text_fragment(text: str, unit_type: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if not compact:
        return ""
    if unit_type == "table" or compact.startswith("|"):
        return _table_fragment(text)
    compact = compact.lstrip("•*-–— ").strip()
    words = compact.split()
    if len(words) < 4:
        return ""
    fragment = " ".join(words[:14]).rstrip(".,;:")
    return fragment if len(fragment) >= 20 else ""


def _source_link(
    payload: dict[str, Any],
    text: str,
    section: list[str],
) -> tuple[str, str, str]:
    url = str(payload.get("source_url") or "").strip()
    if not url:
        return "", "", "document"
    document_url = url.split("#", 1)[0]
    anchor = str(
        payload.get("anchor_id")
        or payload.get("source_dom_id")
        or ""
    ).strip().lstrip("#")
    if anchor:
        if "#" in url:
            return url, document_url, "anchor"
        return (
            f"{url}#{quote(anchor, safe='')}",
            document_url,
            "anchor",
        )
    if "#" in url:
        return url, document_url, "anchor"

    fragment = _text_fragment(text, str(payload.get("unit_type") or ""))
    if not fragment and section:
        heading = section[-1].strip()
        if 4 <= len(heading) <= 120:
            fragment = heading
    if fragment:
        return (
            f"{url}#:~:text={quote(fragment, safe='')}",
            document_url,
            "text_fragment",
        )
    return url, document_url, "document"


def _snippet(text: str, limit: int = 320) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def build_sources(chunks: Sequence[RetrievedChunk]) -> list[dict[str, Any]]:
    """Return citation-aligned source records for the retrieved chunks."""
    sources: list[dict[str, Any]] = []
    for number, chunk in enumerate(chunks, 1):
        payload = chunk.payload
        section = [
            str(value).strip()
            for value in payload.get("hierarchy") or []
            if str(value).strip()
        ]
        if not section:
            part = str(payload.get("part") or "").strip()
            item_id = str(payload.get("item_id") or "").strip()
            item_title = str(payload.get("item_title") or "").strip()
            if part:
                section.append(part)
            if item_id:
                section.append(
                    f"Item {item_id} — {item_title}"
                    if item_title
                    else f"Item {item_id}"
                )
        year = payload.get("year")
        try:
            year = int(year) if year is not None else None
        except (TypeError, ValueError):
            year = None
        url, document_url, link_type = _source_link(
            payload,
            chunk.text,
            section,
        )
        sources.append(
            {
                "citation": number,
                "url": url,
                "document_url": document_url,
                "link_type": link_type,
                "title": _source_title(payload, section),
                "ticker": payload.get("ticker"),
                "cik": payload.get("cik"),
                "form_type": payload.get("form_type"),
                "accession_number": payload.get("accession_number"),
                "document_id": payload.get("document_id"),
                "year": year,
                "part": payload.get("part"),
                "item_id": payload.get("item_id"),
                "item_title": payload.get("item_title"),
                "section": section,
                "anchor_id": payload.get("anchor_id"),
                "source_dom_id": payload.get("source_dom_id"),
                "point_id": chunk.point_id,
                "snippet": _snippet(chunk.text),
                "relevance_score": chunk.rerank_score,
            }
        )
    return sources


def format_retrieved_context(chunks: Sequence[RetrievedChunk]) -> str:
    if not chunks:
        return ""
    blocks: list[str] = []
    for number, chunk in enumerate(chunks, 1):
        payload = chunk.payload
        ticker = str(payload.get("ticker") or "").strip()
        form_type = str(payload.get("form_type") or "").strip()
        part = str(payload.get("part") or "").strip()
        item_id = str(payload.get("item_id") or "").strip()
        item_title = str(payload.get("item_title") or "").strip()
        hierarchy = [
            str(value).strip()
            for value in payload.get("hierarchy") or []
            if str(value).strip()
        ]

        labels = [value for value in (ticker, form_type) if value]
        lines = [f"[{number}] " + " | ".join(labels)]
        if hierarchy:
            lines.append("Section: " + " > ".join(hierarchy))
        else:
            scope = [value for value in (part,) if value]
            if item_id:
                scope.append(
                    f"Item {item_id} — {item_title}"
                    if item_title
                    else f"Item {item_id}"
                )
            if scope:
                lines.append("Section: " + " > ".join(scope))
        lines.append(chunk.text)
        source_url = str(payload.get("source_url") or "").strip()
        if source_url:
            lines.append(f"Source: {source_url}")
        blocks.append("\n".join(line for line in lines if line.strip()))
    return "\n\n".join(blocks)
