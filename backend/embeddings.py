"""Embedding clients for the SEC sentence index.

Vectors are produced in batches and kept out of Qdrant payloads. Tests inject a
deterministic embedder, so no network access is required.
"""

from __future__ import annotations

import hashlib
import inspect
import math
import os
import threading
from typing import Awaitable, Callable, Iterable, Sequence

import httpx
import requests

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"


class EmbeddingError(Exception):
    """Raised when an embedding request fails or returns malformed data."""


class EmbeddingConfigError(EmbeddingError):
    """Raised when the embedding endpoint or credentials are missing."""


EmbedFn = Callable[[list[str]], list[list[float]]]
AsyncEmbedFn = Callable[
    [list[str]], Awaitable[list[list[float]]] | list[list[float]]
]


def embeddings_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/openai/v1"):
        return f"{base}/embeddings"
    return f"{base}/openai/v1/embeddings"


class EmbeddingClient:
    """OpenAI-compatible embeddings client with batch handling."""

    def __init__(
        self,
        embed_fn: EmbedFn | None = None,
        *,
        url: str | None = None,
        api_key: str | None = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 64,
        timeout: float = 60.0,
        session: requests.Session | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self.embed_fn = embed_fn
        self.url = url or os.getenv("EMBED_URL") or ""
        self.api_key = api_key or os.getenv("OPENAI_EMBEDDINGS") or ""
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout
        self._session = session
        self._local = threading.local()
        if self.embed_fn is None:
            if not self.url:
                raise EmbeddingConfigError("EMBED_URL is not configured")
            if not self.api_key:
                raise EmbeddingConfigError("OPENAI_EMBEDDINGS is not configured")
            self.url = embeddings_url(self.url)

    @property
    def session(self) -> requests.Session:
        if self._session is not None:
            return self._session
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            self._local.session = session
        return session

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.embed_fn is not None:
            vectors = self.embed_fn(list(texts))
            return self._validate(vectors, len(texts))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            vectors.extend(self._request_batch(batch))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vectors = self.embed_documents([text])
        if not vectors:
            raise EmbeddingError("embedding response was empty")
        return vectors[0]

    def _request_batch(self, batch: list[str]) -> list[list[float]]:
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        body = {"model": self.model, "input": batch}
        try:
            response = self.session.post(
                self.url,
                headers=headers,
                json=body,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingError("embedding response was not JSON") from exc
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError("embedding response count does not match input")
        ordered: list[tuple[int, list[float]]] = []
        for item in data:
            try:
                index = int(item["index"])
                vector = [float(value) for value in item["embedding"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError("malformed embedding item") from exc
            ordered.append((index, vector))
        ordered.sort(key=lambda pair: pair[0])
        vectors = [vector for _index, vector in ordered]
        return self._validate(vectors, len(batch))

    @staticmethod
    def _validate(vectors: Iterable[Sequence[float]], expected: int) -> list[list[float]]:
        result = [[float(value) for value in vector] for vector in vectors]
        if len(result) != expected:
            raise EmbeddingError(
                f"expected {expected} vectors, received {len(result)}"
            )
        if result and not result[0]:
            raise EmbeddingError("embedding vector is empty")
        dimension = len(result[0]) if result else 0
        for vector in result:
            if len(vector) != dimension:
                raise EmbeddingError("embedding dimensions are inconsistent")
        return result


class AsyncEmbeddingClient:
    """Native-async OpenAI-compatible embeddings client.

    Uses ``httpx.AsyncClient`` so batches yield to the event loop while the
    network request is in flight.
    """

    def __init__(
        self,
        embed_fn: AsyncEmbedFn | None = None,
        *,
        url: str | None = None,
        api_key: str | None = None,
        model: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 32,
        timeout: float = 60.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        self.embed_fn = embed_fn
        self.url = url or os.getenv("EMBED_URL") or ""
        self.api_key = api_key or os.getenv("OPENAI_EMBEDDINGS") or ""
        self.model = model
        self.batch_size = batch_size
        self.timeout = timeout
        self._client = client
        self._owns_client = client is None
        if self.embed_fn is None:
            if not self.url:
                raise EmbeddingConfigError("EMBED_URL is not configured")
            if not self.api_key:
                raise EmbeddingConfigError("OPENAI_EMBEDDINGS is not configured")
            self.url = embeddings_url(self.url)

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        if self.embed_fn is not None:
            result = self.embed_fn(list(texts))
            vectors = await result if inspect.isawaitable(result) else result
            return EmbeddingClient._validate(vectors, len(texts))
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            batch = list(texts[start : start + self.batch_size])
            vectors.extend(await self._request_batch(batch))
        return vectors

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self.embed_documents([text])
        if not vectors:
            raise EmbeddingError("embedding response was empty")
        return vectors[0]

    async def _request_batch(self, batch: list[str]) -> list[list[float]]:
        headers = {
            "api-key": self.api_key,
            "Content-Type": "application/json",
        }
        body = {"model": self.model, "input": batch}
        try:
            response = await self.client.post(
                self.url,
                headers=headers,
                json=body,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise EmbeddingError(f"embedding request failed: {exc}") from exc
        try:
            payload = response.json()
        except ValueError as exc:
            raise EmbeddingError("embedding response was not JSON") from exc
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != len(batch):
            raise EmbeddingError("embedding response count does not match input")
        ordered: list[tuple[int, list[float]]] = []
        for item in data:
            try:
                index = int(item["index"])
                vector = [float(value) for value in item["embedding"]]
            except (KeyError, TypeError, ValueError) as exc:
                raise EmbeddingError("malformed embedding item") from exc
            ordered.append((index, vector))
        ordered.sort(key=lambda pair: pair[0])
        vectors = [vector for _index, vector in ordered]
        return EmbeddingClient._validate(vectors, len(batch))

    async def close(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
        self._client = None


class DeterministicHashEmbedder:
    """Offline, deterministic embedder used by tests and dry runs."""

    def __init__(self, dimension: int = 64) -> None:
        if dimension < 1:
            raise ValueError("dimension must be at least 1")
        self.dimension = dimension

    def __call__(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(text) for text in texts]

    def embed(self, text: str) -> list[float]:
        values: list[float] = []
        counter = 0
        while len(values) < self.dimension:
            digest = hashlib.sha256(
                f"{counter}:{text}".encode("utf-8")
            ).digest()
            for byte in digest:
                values.append((byte / 127.5) - 1.0)
                if len(values) == self.dimension:
                    break
            counter += 1
        norm = math.sqrt(sum(value * value for value in values))
        if norm == 0:
            return values
        return [value / norm for value in values]
