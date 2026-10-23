"""Redis cache for SEC ticker resolution and ingestion results.

The cache is optional at runtime: if Redis is unavailable, reads miss and
writes are dropped so ingestion still works. Values are JSON objects.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

try:
    import redis
except ImportError:  # pragma: no cover - redis is a declared dependency
    redis = None  # type: ignore[assignment]

LOGGER = logging.getLogger("cache")

DEFAULT_REDIS_URL = "redis://localhost:6379/0"
DEFAULT_TTL_SECONDS = 7 * 24 * 60 * 60
CACHE_VERSION = "v1"
_SOCKET_TIMEOUT = 1.0


def ticker_cache_key(ticker: str) -> str:
    return f"sec:ticker:{CACHE_VERSION}:{ticker.strip().upper()}"


class RedisCache:
    """Small JSON cache wrapper with fail-open behaviour."""

    def __init__(
        self,
        url: str | None = None,
        *,
        ttl: int | None = None,
        client: Any | None = None,
    ) -> None:
        self.url = url or os.getenv("REDIS_URL") or DEFAULT_REDIS_URL
        try:
            configured_ttl = (
                int(os.getenv("REDIS_TTL_SECONDS") or DEFAULT_TTL_SECONDS)
                if ttl is None
                else int(ttl)
            )
        except (TypeError, ValueError):
            configured_ttl = DEFAULT_TTL_SECONDS
        self.ttl = max(0, configured_ttl)
        self._client = client
        self._warned = False

    @property
    def client(self) -> Any:
        if self._client is None:
            if redis is None:
                raise RuntimeError("redis package is not installed")
            self._client = redis.Redis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=_SOCKET_TIMEOUT,
                socket_timeout=_SOCKET_TIMEOUT,
            )
        return self._client

    def _warn(self, action: str, exc: Exception) -> None:
        if not self._warned:
            LOGGER.warning("redis %s failed (%s); continuing uncached", action, exc)
            self._warned = True

    def get_json(self, key: str) -> dict | None:
        try:
            raw = self.client.get(key)
        except Exception as exc:
            self._warn("read", exc)
            return None
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (TypeError, ValueError) as exc:
            LOGGER.warning("ignoring malformed cache entry %s: %s", key, exc)
            return None
        return value if isinstance(value, dict) else None

    def set_json(self, key: str, value: dict) -> bool:
        try:
            serialized = json.dumps(value, ensure_ascii=False)
        except (TypeError, ValueError) as exc:
            LOGGER.warning("cache value for %s is not JSON-serializable: %s", key, exc)
            return False
        try:
            self.client.set(key, serialized, ex=self.ttl or None)
        except Exception as exc:
            self._warn("write", exc)
            return False
        return True

    def delete(self, key: str) -> bool:
        try:
            self.client.delete(key)
        except Exception as exc:
            self._warn("delete", exc)
            return False
        return True

    def close(self) -> None:
        if self._client is None:
            return
        try:
            self._client.close()
        except Exception:
            pass
