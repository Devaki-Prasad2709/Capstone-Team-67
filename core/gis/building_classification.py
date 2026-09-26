"""Persistent manual building classifications stored separately from raw GIS."""

from __future__ import annotations

from datetime import datetime, timezone
import copy
from pathlib import Path
import re
import sqlite3
from threading import RLock

from config.settings import settings


TYPE_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class BuildingClassificationStore:
    """Append-only SQLite revision ledger keyed by stable building source ID."""

    def __init__(
        self,
        database_path: Path | None = None,
        *,
        building_source_ids=None,
        clock=None,
    ) -> None:
        self.database_path = Path(
            database_path or settings.resolved_building_classification_database_path
        )
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._known_ids = set(building_source_ids) if building_source_ids is not None else None
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._lock = RLock()
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def _initialize(self):
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS building_classification_revisions (
                    revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    building_source_id TEXT NOT NULL,
                    revision_number INTEGER NOT NULL,
                    assigned_type TEXT NOT NULL,
                    operator TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    notes TEXT,
                    UNIQUE(building_source_id, revision_number)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_building_classification_latest "
                "ON building_classification_revisions"
                "(building_source_id, revision_number DESC)"
            )

    @staticmethod
    def _required_text(value, field, max_length=500):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a nonempty string")
        value = value.strip()
        if len(value) > max_length:
            raise ValueError(f"{field} must be at most {max_length} characters")
        return value

    def _building_id(self, value):
        building_id = self._required_text(value, "building_source_id", 300)
        if self._known_ids is not None and building_id not in self._known_ids:
            raise KeyError(f"Unknown GIS building source ID: {building_id}")
        return building_id

    def recognizes(self, building_source_id: object) -> bool:
        """Return whether an ID belongs to this store's registered GIS snapshot."""
        return (
            isinstance(building_source_id, str)
            and (self._known_ids is None or building_source_id in self._known_ids)
        )

    @staticmethod
    def _record(row):
        if row is None:
            return None
        return {
            "revision_id": int(row["revision_id"]),
            "building_source_id": row["building_source_id"],
            "revision_number": int(row["revision_number"]),
            "assigned_type": row["assigned_type"],
            "operator": row["operator"],
            "timestamp": row["assigned_at"],
            "notes": row["notes"],
        }

    def assign(
        self,
        building_source_id: str,
        assigned_type: str,
        *,
        operator: str,
        notes: str | None = None,
        timestamp: str | None = None,
    ) -> dict:
        building_id = self._building_id(building_source_id)
        normalized_type = self._required_text(assigned_type, "assigned_type", 64).lower()
        normalized_type = re.sub(r"[\s-]+", "_", normalized_type)
        if not TYPE_PATTERN.fullmatch(normalized_type):
            raise ValueError(
                "assigned_type must start with a letter and contain only letters, "
                "numbers, underscores, or hyphens"
            )
        operator = self._required_text(operator, "operator", 200)
        if notes is not None:
            notes = notes.strip()
            if len(notes) > 2000:
                raise ValueError("notes must be at most 2000 characters")
            notes = notes or None
        assigned_at = timestamp or self._clock()
        assigned_at = self._required_text(assigned_at, "timestamp", 100)
        try:
            datetime.fromisoformat(assigned_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("timestamp must be ISO-8601") from exc

        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            latest = connection.execute(
                "SELECT COALESCE(MAX(revision_number), 0) FROM "
                "building_classification_revisions WHERE building_source_id = ?",
                (building_id,),
            ).fetchone()[0]
            cursor = connection.execute(
                "INSERT INTO building_classification_revisions "
                "(building_source_id, revision_number, assigned_type, operator, assigned_at, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (building_id, int(latest) + 1, normalized_type, operator, assigned_at, notes),
            )
            row = connection.execute(
                "SELECT * FROM building_classification_revisions WHERE revision_id = ?",
                (cursor.lastrowid,),
            ).fetchone()
        return self._record(row)

    def get(self, building_source_id: str) -> dict | None:
        building_id = self._building_id(building_source_id)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM building_classification_revisions "
                "WHERE building_source_id = ? ORDER BY revision_number DESC LIMIT 1",
                (building_id,),
            ).fetchone()
        return self._record(row)

    def history(self, building_source_id: str) -> list[dict]:
        building_id = self._building_id(building_source_id)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM building_classification_revisions "
                "WHERE building_source_id = ? ORDER BY revision_number ASC",
                (building_id,),
            ).fetchall()
        return [self._record(row) for row in rows]

    def list_current(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT revisions.*
                FROM building_classification_revisions AS revisions
                JOIN (
                    SELECT building_source_id, MAX(revision_number) AS latest_revision
                    FROM building_classification_revisions
                    GROUP BY building_source_id
                ) AS latest
                ON latest.building_source_id = revisions.building_source_id
                AND latest.latest_revision = revisions.revision_number
                ORDER BY revisions.building_source_id
                """
            ).fetchall()
        return [self._record(row) for row in rows]


def apply_building_classifications(graph, store: BuildingClassificationStore):
    """Return a graph copy enriched from the overlay store, never raw OSM."""
    updated = copy.deepcopy(graph)
    for node_id, node in updated.nodes(data=True):
        source_id = node.get("gis_source_id")
        if (
            node.get("type") == "social"
            and isinstance(source_id, str)
            and store.recognizes(source_id)
        ):
            classification = store.get(source_id)
            node["building_classification"] = classification
            node["assigned_building_type"] = (
                classification["assigned_type"] if classification else None
            )
    return updated
