"""Small thread-safe caches for dashboard external-service projections."""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from threading import RLock
from typing import Callable


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class TopicTailCache:
    """Reuse bounded Kafka tails across widgets and snapshot refreshes."""

    def __init__(self, reader: Callable, *, ttl_seconds: float = 10.0, clock=None):
        self.reader = reader
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.monotonic
        self._entries: dict[str, dict] = {}
        self._lock = RLock()

    def get(self, topic: str, limit: int) -> tuple[list[dict], str | None, dict]:
        started = self.clock()
        with self._lock:
            entry = self._entries.get(topic)
            now = self.clock()
            if entry and now - entry["stored_at"] < self.ttl_seconds and entry["limit"] >= limit:
                return (
                    copy.deepcopy(entry["rows"][:limit]),
                    entry["error"],
                    self._metadata(entry, True, started),
                )

            rows, error = self.reader(topic, limit)
            entry = {
                "rows": copy.deepcopy(rows),
                "error": error,
                "limit": limit,
                "stored_at": self.clock(),
                "cached_at": utc_now(),
            }
            self._entries[topic] = entry
            return copy.deepcopy(rows), error, self._metadata(entry, False, started)

    def get_many(self, requests: dict[str, int]) -> tuple[dict[str, list[dict]], dict, dict]:
        rows: dict[str, list[dict]] = {}
        errors: dict[str, str | None] = {}
        metadata: dict[str, dict] = {}
        for topic, limit in requests.items():
            rows[topic], errors[topic], metadata[topic] = self.get(topic, limit)
        return rows, errors, metadata

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _metadata(self, entry: dict, hit: bool, started: float) -> dict:
        return {
            "cache_hit": hit,
            "cached_at": entry["cached_at"],
            "cache_age_ms": round((self.clock() - entry["stored_at"]) * 1000, 3),
            "request_duration_ms": round((self.clock() - started) * 1000, 3),
        }


class HealthCache:
    """Cache Docker, Kafka and MinIO health independently with timestamps."""

    def __init__(self, *, ttl_seconds: float = 10.0, clock=None):
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.monotonic
        self._entries: dict[str, dict] = {}
        self._lock = RLock()

    def get(self, name: str, loader: Callable[[], tuple]) -> tuple[object, object, dict]:
        started = self.clock()
        with self._lock:
            entry = self._entries.get(name)
            now = self.clock()
            hit = bool(entry and now - entry["stored_at"] < self.ttl_seconds)
            if not hit:
                value, error = loader()
                entry = {
                    "value": copy.deepcopy(value),
                    "error": error,
                    "stored_at": self.clock(),
                    "cached_at": utc_now(),
                }
                self._entries[name] = entry
            return (
                copy.deepcopy(entry["value"]),
                entry["error"],
                {
                    "cache_hit": hit,
                    "cached_at": entry["cached_at"],
                    "cache_age_ms": round((self.clock() - entry["stored_at"]) * 1000, 3),
                    "request_duration_ms": round((self.clock() - started) * 1000, 3),
                },
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
