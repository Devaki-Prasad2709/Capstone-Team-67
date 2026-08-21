"""Persistent exact and perceptual image deduplication."""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from config.settings import settings


@dataclass(frozen=True)
class DeduplicationResult:
    is_duplicate: bool
    canonical_id: str
    content_hash: str
    canonical_content_hash: str
    perceptual_hash: str | None
    duplicate_method: str | None = None
    perceptual_distance: int | None = None


def content_hash(data: bytes) -> str:
    """Return a stable byte-for-byte fingerprint."""
    return hashlib.sha256(data).hexdigest()


def perceptual_hash(image: np.ndarray) -> str | None:
    """Return a 64-bit pHash that tolerates resizing and JPEG recompression."""
    if image is None or image.size == 0:
        return None
    if image.ndim == 3:
        if image.shape[2] == 4:
            image = cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
        else:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(image, (32, 32), interpolation=cv2.INTER_AREA).astype(np.float32)
    transformed = cv2.dct(resized)
    low_frequency = transformed[:8, :8].copy()
    values = low_frequency.flatten()[1:]
    median = float(np.median(values))
    bits = low_frequency.flatten() > median
    value = 0
    for bit in bits:
        value = (value << 1) | int(bit)
    return f"{value:016x}"


def hamming_distance(first: str, second: str) -> int:
    return (int(first, 16) ^ int(second, 16)).bit_count()


class ImageDeduplicator:
    """Track canonical images in SQLite across producer restarts."""

    _lock = threading.Lock()

    def __init__(self, database_path: Path | None = None) -> None:
        self.database_path = database_path or settings.resolved_dedup_database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS image_fingerprints (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL,
                    canonical_id TEXT NOT NULL,
                    content_hash TEXT NOT NULL UNIQUE,
                    perceptual_hash TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_fingerprint_source_time "
                "ON image_fingerprints(source, created_at DESC)"
            )

    def check_and_register(
        self,
        source: str,
        image_id: str,
        image_bytes: bytes,
        decoded_image: np.ndarray | None,
    ) -> DeduplicationResult:
        """Find a canonical match or atomically register a new canonical image."""
        sha256 = content_hash(image_bytes)
        phash = perceptual_hash(decoded_image) if decoded_image is not None else None
        if not settings.deduplication_enabled:
            return DeduplicationResult(False, image_id, sha256, sha256, phash)

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            exact = connection.execute(
                "SELECT canonical_id, perceptual_hash, content_hash FROM image_fingerprints "
                "WHERE content_hash = ?",
                (sha256,),
            ).fetchone()
            if exact:
                return DeduplicationResult(
                    True, exact[0], sha256, exact[2], phash or exact[1], "sha256", 0
                )

            near_enabled = (
                settings.near_duplicate_enabled
                and source.lower() in settings.near_duplicate_sources
                and phash is not None
            )
            if near_enabled:
                candidates = connection.execute(
                    "SELECT canonical_id, perceptual_hash, content_hash FROM image_fingerprints "
                    "WHERE source = ? AND perceptual_hash IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT ?",
                    (source, max(settings.dedup_lookback_records, 1)),
                ).fetchall()
                best_id: str | None = None
                best_content_hash: str | None = None
                best_distance: int | None = None
                for canonical_id, candidate_hash, candidate_content_hash in candidates:
                    distance = hamming_distance(phash, candidate_hash)
                    if best_distance is None or distance < best_distance:
                        best_id = canonical_id
                        best_content_hash = candidate_content_hash
                        best_distance = distance
                if best_distance is not None and best_distance <= max(
                    settings.phash_distance_threshold, 0
                ):
                    return DeduplicationResult(
                        True,
                        best_id or image_id,
                        sha256,
                        best_content_hash or sha256,
                        phash,
                        "phash",
                        best_distance,
                    )

            connection.execute(
                "INSERT INTO image_fingerprints "
                "(source, canonical_id, content_hash, perceptual_hash, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, image_id, sha256, phash, time.time()),
            )
        return DeduplicationResult(False, image_id, sha256, sha256, phash)

    def unregister_canonical(self, source: str, canonical_id: str) -> None:
        """Remove an uncommitted fingerprint after transfer/Kafka failure."""
        if not settings.deduplication_enabled:
            return
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM image_fingerprints WHERE source = ? AND canonical_id = ?",
                (source, canonical_id),
            )
