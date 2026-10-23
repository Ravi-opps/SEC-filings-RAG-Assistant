"""Qdrant storage for sentence records.

Vectors live in the point vector field; sentence text and filing metadata live
in the payload. IDs are deterministic UUIDs derived from the record point ID.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Sequence

from qdrant_client import AsyncQdrantClient, QdrantClient, models

from sentence_records import SentenceRecord

DEFAULT_COLLECTION = "sec_sentences"
POINT_NAMESPACE = uuid.NAMESPACE_URL


class StorageError(Exception):
    """Raised when Qdrant storage is used incorrectly or fails."""


@dataclass(frozen=True)
class SearchHit:
    point_id: str
    score: float
    payload: dict


def qdrant_point_id(point_id: str) -> str:
    return str(uuid.uuid5(POINT_NAMESPACE, f"sec-sentence:{point_id}"))


class QdrantStore:
    def __init__(
        self,
        client: QdrantClient | None = None,
        *,
        collection_name: str | None = None,
        url: str | None = None,
        api_key: str | None = None,
        path: str | None = None,
        timeout: int | None = 120,
        distance: models.Distance = models.Distance.COSINE,
    ) -> None:
        self.collection_name = (
            collection_name or os.getenv("QDRANT_COLLECTION") or DEFAULT_COLLECTION
        )
        self.distance = distance
        self.vector_size: int | None = None
        self._ensured = False
        if client is not None:
            self.client = client
            return
        resolved_url = url or os.getenv("QDRANT_URL")
        resolved_key = (
            api_key
            or os.getenv("QDRANT_API_KEY")
            or os.getenv("QDRANT_API")
        )
        if resolved_url:
            self.client = QdrantClient(
                url=resolved_url,
                api_key=resolved_key,
                timeout=timeout,
            )
        elif path:
            self.client = QdrantClient(path=path, timeout=timeout)
        else:
            self.client = QdrantClient(location=":memory:", timeout=timeout)

    def collection_exists(self) -> bool:
        return bool(self.client.collection_exists(self.collection_name))

    def wipe(self) -> None:
        """Drop the collection and forget cached sizing/ensure state."""
        if self.collection_exists():
            self.client.delete_collection(self.collection_name)
        self.vector_size = None
        self._ensured = False

    def ensure_collection(
        self,
        vector_size: int,
        *,
        recreate: bool = False,
    ) -> None:
        if vector_size < 1:
            raise StorageError("vector_size must be at least 1")
        exists = self.collection_exists()
        if recreate and exists:
            self.client.delete_collection(self.collection_name)
            exists = False
        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=models.VectorParams(
                    size=vector_size,
                    distance=self.distance,
                ),
            )
        elif self.vector_size and vector_size != self.vector_size:
            raise StorageError(
                f"collection vector size is {self.vector_size}, got {vector_size}"
            )
        self.vector_size = vector_size
        self._ensured = True

    def upsert(
        self,
        records: Sequence[SentenceRecord],
        vectors: Sequence[Sequence[float]],
        *,
        batch_size: int = 32,
        wait: bool = True,
        max_workers: int = 4,
    ) -> int:
        if not self._ensured or self.vector_size is None:
            raise StorageError("ensure_collection must be called before upsert")
        if len(records) != len(vectors):
            raise StorageError("records and vectors must have the same length")
        for vector in vectors:
            if len(vector) != self.vector_size:
                raise StorageError(
                    f"vector size {len(vector)} does not match collection "
                    f"size {self.vector_size}"
                )
        points = [
            models.PointStruct(
                id=qdrant_point_id(record.point_id),
                vector=[float(value) for value in vector],
                payload=record.to_payload(),
            )
            for record, vector in zip(records, vectors)
        ]
        batches = [
            points[start : start + batch_size]
            for start in range(0, len(points), batch_size)
        ]

        def upsert_batch(batch: list[models.PointStruct]) -> None:
            for attempt in range(3):
                try:
                    self.client.upsert(
                        collection_name=self.collection_name,
                        points=batch,
                        wait=wait,
                    )
                    return
                except Exception:
                    if attempt == 2:
                        raise
                    time.sleep(1.5 * (attempt + 1))

        if len(batches) == 1:
            upsert_batch(batches[0])
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [pool.submit(upsert_batch, batch) for batch in batches]
                for future in as_completed(futures):
                    future.result()
        return len(points)

    def count(self, *, document_id: str | None = None) -> int:
        query_filter = None
        if document_id:
            query_filter = models.Filter(
                must=[
                    models.FieldCondition(
                        key="document_id",
                        match=models.MatchValue(value=document_id),
                    )
                ]
            )
        result = self.client.count(
            collection_name=self.collection_name,
            count_filter=query_filter,
            exact=True,
        )
        return int(result.count)

    def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int = 5,
        document_id: str | None = None,
        item_id: str | None = None,
        part: str | None = None,
    ) -> list[SearchHit]:
        conditions: list[models.Condition] = []
        for key, value in (
            ("document_id", document_id),
            ("item_id", item_id),
            ("part", part),
        ):
            if value is not None:
                conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=value),
                    )
                )
        query_filter = models.Filter(must=conditions) if conditions else None
        response = self.client.query_points(
            collection_name=self.collection_name,
            query=[float(value) for value in query_vector],
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        hits: list[SearchHit] = []
        for point in response.points:
            payload = dict(point.payload or {})
            hits.append(
                SearchHit(
                    point_id=str(payload.get("point_id") or point.id),
                    score=float(point.score),
                    payload=payload,
                )
            )
        return hits

    def delete_document(self, document_id: str, *, wait: bool = True) -> None:
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="document_id",
                            match=models.MatchValue(value=document_id),
                        )
                    ]
                )
            ),
            wait=wait,
        )

    def close(self) -> None:
        self.client.close()


class AsyncQdrantStore:
    """Native-async Qdrant storage using ``AsyncQdrantClient``."""

    def __init__(
        self,
        client: AsyncQdrantClient | None = None,
        *,
        collection_name: str | None = None,
        url: str | None = None,
        api_key: str | None = None,
        path: str | None = None,
        timeout: int | None = 120,
        distance: models.Distance = models.Distance.COSINE,
    ) -> None:
        self.collection_name = (
            collection_name or os.getenv("QDRANT_COLLECTION") or DEFAULT_COLLECTION
        )
        self.distance = distance
        self.vector_size: int | None = None
        self._ensured = False
        self._ensure_lock = asyncio.Lock()
        self._owns_client = client is None
        if client is not None:
            self.client = client
            return
        resolved_url = url or os.getenv("QDRANT_URL")
        resolved_key = (
            api_key
            or os.getenv("QDRANT_API_KEY")
            or os.getenv("QDRANT_API")
        )
        if resolved_url:
            self.client = AsyncQdrantClient(
                url=resolved_url,
                api_key=resolved_key,
                timeout=timeout,
            )
        elif path:
            self.client = AsyncQdrantClient(path=path, timeout=timeout)
        else:
            self.client = AsyncQdrantClient(
                location=":memory:",
                timeout=timeout,
            )

    async def collection_exists(self) -> bool:
        return bool(
            await self.client.collection_exists(self.collection_name)
        )

    async def ensure_collection(
        self,
        vector_size: int,
        *,
        recreate: bool = False,
    ) -> None:
        if vector_size < 1:
            raise StorageError("vector_size must be at least 1")
        async with self._ensure_lock:
            exists = await self.collection_exists()
            if recreate and exists:
                await self.client.delete_collection(self.collection_name)
                exists = False
            if not exists:
                await self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=models.VectorParams(
                        size=vector_size,
                        distance=self.distance,
                    ),
                )
            elif self.vector_size and vector_size != self.vector_size:
                raise StorageError(
                    f"collection vector size is {self.vector_size}, "
                    f"got {vector_size}"
                )
            self.vector_size = vector_size
            self._ensured = True

    async def upsert(
        self,
        records: Sequence[SentenceRecord],
        vectors: Sequence[Sequence[float]],
        *,
        batch_size: int = 32,
        wait: bool = True,
        max_concurrency: int = 4,
    ) -> int:
        if not self._ensured or self.vector_size is None:
            raise StorageError("ensure_collection must be called before upsert")
        if len(records) != len(vectors):
            raise StorageError("records and vectors must have the same length")
        for vector in vectors:
            if len(vector) != self.vector_size:
                raise StorageError(
                    f"vector size {len(vector)} does not match collection "
                    f"size {self.vector_size}"
                )
        points = [
            models.PointStruct(
                id=qdrant_point_id(record.point_id),
                vector=[float(value) for value in vector],
                payload=record.to_payload(),
            )
            for record, vector in zip(records, vectors)
        ]
        batches = [
            points[start : start + batch_size]
            for start in range(0, len(points), batch_size)
        ]
        semaphore = asyncio.Semaphore(max(1, max_concurrency))

        async def upsert_batch(batch: list[models.PointStruct]) -> None:
            async with semaphore:
                for attempt in range(3):
                    try:
                        await self.client.upsert(
                            collection_name=self.collection_name,
                            points=batch,
                            wait=wait,
                        )
                        return
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(1.5 * (attempt + 1))

        await asyncio.gather(*(upsert_batch(batch) for batch in batches))
        return len(points)

    async def count(
        self,
        *,
        document_id: str | None = None,
        accession_number: str | None = None,
    ) -> int:
        if not await self.collection_exists():
            return 0
        conditions: list[models.Condition] = []
        for key, value in (
            ("document_id", document_id),
            ("accession_number", accession_number),
        ):
            if value:
                conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=value),
                    )
                )
        if not conditions:
            return 0
        result = await self.client.count(
            collection_name=self.collection_name,
            count_filter=models.Filter(must=conditions),
            exact=True,
        )
        return int(result.count)

    async def search(
        self,
        query_vector: Sequence[float],
        *,
        limit: int = 20,
        document_id: str | None = None,
        item_id: str | None = None,
        part: str | None = None,
    ) -> list[SearchHit]:
        if limit < 1:
            raise StorageError("limit must be at least 1")
        if not await self.collection_exists():
            return []
        conditions: list[models.Condition] = []
        for key, value in (
            ("document_id", document_id),
            ("item_id", item_id),
            ("part", part),
        ):
            if value is not None:
                conditions.append(
                    models.FieldCondition(
                        key=key,
                        match=models.MatchValue(value=value),
                    )
                )
        query_filter = models.Filter(must=conditions) if conditions else None
        response = await self.client.query_points(
            collection_name=self.collection_name,
            query=[float(value) for value in query_vector],
            query_filter=query_filter,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        hits: list[SearchHit] = []
        for point in response.points:
            payload = dict(point.payload or {})
            hits.append(
                SearchHit(
                    point_id=str(payload.get("point_id") or point.id),
                    score=float(point.score),
                    payload=payload,
                )
            )
        return hits

    async def close(self) -> None:
        if self._owns_client:
            await self.client.close()
