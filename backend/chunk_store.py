"""Redis persistence for structure-aware filing chunks."""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any

import redis
import redis.asyncio as redis_async

from sentence_records import ParsedFiling

DEFAULT_CHUNK_TTL_SECONDS = 7 * 24 * 60 * 60
CHUNK_KEY_VERSION = "v1"
INDEXED_KEY_VERSION = "v1"
_SOCKET_TIMEOUT = 30.0


def document_chunks_key(document_id: str) -> str:
    return f"sec:doc:{CHUNK_KEY_VERSION}:{document_id}:chunks"


def document_meta_key(document_id: str) -> str:
    return f"sec:doc:{CHUNK_KEY_VERSION}:{document_id}:meta"


def document_status_key(document_id: str) -> str:
    return f"sec:doc:{CHUNK_KEY_VERSION}:{document_id}:status"


def chunk_key(document_id: str, point_id: str) -> str:
    return f"sec:chunk:{CHUNK_KEY_VERSION}:{document_id}:{point_id}"


def indexed_document_key(
    document_id: str,
    accession_number: str | None = None,
) -> str:
    identity = (accession_number or "").strip() or document_id
    return f"sec:indexed:{INDEXED_KEY_VERSION}:{identity}"


class ChunkStore:
    """Persist every chunk as JSON and keep ordered document indexes."""

    def __init__(
        self,
        url: str | None = None,
        *,
        ttl: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.url = url or os.getenv("REDIS_URL") or "redis://localhost:6379/0"
        try:
            configured_ttl = (
                int(
                    os.getenv("CHUNK_TTL_SECONDS")
                    or DEFAULT_CHUNK_TTL_SECONDS
                )
                if ttl is None
                else int(ttl)
            )
        except (TypeError, ValueError):
            configured_ttl = DEFAULT_CHUNK_TTL_SECONDS
        self.ttl = max(0, configured_ttl)
        self._client = client
        self._async_client: Any | None = None

    @property
    def client(self) -> Any:
        if self._client is None:
            self._client = redis.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT,
                socket_timeout=_SOCKET_TIMEOUT,
            )
        return self._client

    @property
    def async_client(self) -> Any:
        if self._async_client is None:
            self._async_client = redis_async.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT,
                socket_timeout=_SOCKET_TIMEOUT,
            )
        return self._async_client

    def save_parsed(
        self,
        parsed: ParsedFiling,
        *,
        source_type: str = "sec",
    ) -> int:
        document_id = parsed.meta.document_id
        records = parsed.records
        chunk_ids_key = document_chunks_key(document_id)
        old_point_ids = self.client.zrange(chunk_ids_key, 0, -1)
        pipe = self.client.pipeline(transaction=False)
        if old_point_ids:
            pipe.delete(
                *(chunk_key(document_id, point_id) for point_id in old_point_ids)
            )
        pipe.delete(chunk_ids_key)
        pipe.delete(
            indexed_document_key(document_id, parsed.meta.accession_number)
        )
        for record in records:
            record.source_type = source_type
            payload = record.to_payload()
            pipe.set(
                chunk_key(document_id, record.point_id),
                json.dumps(payload, ensure_ascii=False),
                ex=self.ttl or None,
            )
        if records:
            pipe.zadd(
                chunk_ids_key,
                {
                    record.point_id: record.order_index
                    for record in records
                },
            )
            if self.ttl:
                pipe.expire(chunk_ids_key, self.ttl)
        meta = {
            **asdict(parsed.meta),
            "title": parsed.title,
            "chunk_count": len(records),
            "unit_counts": parsed.unit_counts,
            "parser_version": parsed.parser_version,
            "source_type": source_type,
            "links": len(parsed.links),
            "links_resolved": sum(
                1 for link in parsed.links if link.resolved
            ),
        }
        pipe.set(
            document_meta_key(document_id),
            json.dumps(meta, ensure_ascii=False),
            ex=self.ttl or None,
        )
        pipe.set(
            document_status_key(document_id),
            json.dumps(
                {"status": "chunked", "chunk_count": len(records)},
                ensure_ascii=False,
            ),
            ex=self.ttl or None,
        )
        pipe.execute()
        return len(records)

    def delete_chunks(
        self,
        document_id: str,
        point_ids: list[str],
    ) -> int:
        if not point_ids:
            return 0
        pipe = self.client.pipeline(transaction=False)
        pipe.zrem(document_chunks_key(document_id), *point_ids)
        pipe.delete(
            *(chunk_key(document_id, point_id) for point_id in point_ids)
        )
        pipe.execute()
        return len(point_ids)

    async def delete_chunks_async(
        self,
        document_id: str,
        point_ids: list[str],
    ) -> int:
        if not point_ids:
            return 0
        pipe = self.async_client.pipeline(transaction=False)
        pipe.zrem(document_chunks_key(document_id), *point_ids)
        pipe.delete(
            *(chunk_key(document_id, point_id) for point_id in point_ids)
        )
        await pipe.execute()
        return len(point_ids)

    def get_indexed(self, key: str) -> dict | None:
        raw = self.client.get(key)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def get_indexed_async(self, key: str) -> dict | None:
        raw = await self.async_client.get(key)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    async def set_indexed_async(
        self,
        key: str,
        payload: dict,
    ) -> None:
        await self.async_client.set(
            key,
            json.dumps(payload, ensure_ascii=False),
        )

    async def delete_indexed_async(self, key: str) -> None:
        await self.async_client.delete(key)

    def load_chunks(self, document_id: str) -> list[dict]:
        point_ids = self.client.zrange(
            document_chunks_key(document_id), 0, -1
        )
        if not point_ids:
            return []
        keys = [
            chunk_key(document_id, point_id) for point_id in point_ids
        ]
        values = self.client.mget(keys)
        chunks = []
        for value in values:
            if not value:
                continue
            chunks.append(json.loads(value))
        return chunks

    def get_meta(self, document_id: str) -> dict | None:
        raw = self.client.get(document_meta_key(document_id))
        if not raw:
            return None
        value = json.loads(raw)
        return value if isinstance(value, dict) else None

    @staticmethod
    def _status_payload(
        document_id: str,
        status: str,
        details: dict[str, Any],
    ) -> str:
        return json.dumps(
            {
                "status": status,
                "document_id": document_id,
                **details,
            },
            ensure_ascii=False,
        )

    def mark_status(self, document_id: str, status: str, **details: Any) -> None:
        self.client.set(
            document_status_key(document_id),
            self._status_payload(document_id, status, details),
            ex=self.ttl or None,
        )

    async def mark_status_async(
        self,
        document_id: str,
        status: str,
        **details: Any,
    ) -> None:
        await self.async_client.set(
            document_status_key(document_id),
            self._status_payload(document_id, status, details),
            ex=self.ttl or None,
        )

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception:
            pass

    async def close_async(self) -> None:
        if self._async_client is None:
            return
        try:
            await self._async_client.aclose()
        except Exception:
            pass
        self._async_client = None
