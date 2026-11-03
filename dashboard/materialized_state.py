"""Durable, offset-checkpointed source evidence for dashboard projections."""

from __future__ import annotations

import copy
import json
import sqlite3
import time
from pathlib import Path
from threading import RLock

from dashboard.cache_service import utc_now


DEFAULT_PATH = Path(__file__).resolve().parents[1] / ".runtime" / "dashboard-projection.sqlite3"


class DashboardMaterializedState:
    """Persist source events and replay state without publishing derived results."""

    def __init__(
        self,
        reader,
        topics: dict[str, str],
        database_path: Path | None = None,
        *,
        refresh_ttl_seconds: float = 5.0,
    ):
        self.reader = reader
        self.topics = dict(topics)
        self.database_path = Path(database_path or DEFAULT_PATH)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.refresh_ttl_seconds = refresh_ttl_seconds
        self._last_refresh_at = 0.0
        self._last_refresh_result = None
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=20)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS dashboard_events (
                    topic TEXT NOT NULL,
                    partition_id INTEGER NOT NULL,
                    offset_id INTEGER NOT NULL,
                    event_id TEXT,
                    payload_json TEXT NOT NULL,
                    materialized_at TEXT NOT NULL,
                    PRIMARY KEY (topic, partition_id, offset_id)
                );
                CREATE INDEX IF NOT EXISTS idx_dashboard_events_id
                    ON dashboard_events(topic, event_id);
                CREATE TABLE IF NOT EXISTS dashboard_checkpoints (
                    topic TEXT NOT NULL,
                    partition_id INTEGER NOT NULL,
                    next_offset INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (topic, partition_id)
                );
                CREATE TABLE IF NOT EXISTS dashboard_replay_state (
                    scenario_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL,
                    reset_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dashboard_replay_boundaries (
                    scenario_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    topic TEXT NOT NULL,
                    partition_id INTEGER NOT NULL,
                    minimum_offset INTEGER NOT NULL,
                    PRIMARY KEY (scenario_id, generation, topic, partition_id)
                );
                CREATE TABLE IF NOT EXISTS dashboard_social_reviews (
                    scope TEXT NOT NULL,
                    scenario_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    alert_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    responder_id TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    PRIMARY KEY (scope, scenario_id, generation, alert_id)
                );
                CREATE TABLE IF NOT EXISTS dashboard_processed_evidence (
                    scenario_id TEXT NOT NULL,
                    generation INTEGER NOT NULL,
                    evidence_type TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    PRIMARY KEY (scenario_id, generation, evidence_type, evidence_id)
                );
                """
            )

    def refresh(self, *, force: bool = False) -> dict:
        """Incrementally materialize every configured topic from its next offset."""
        started = time.perf_counter()
        sources = {}
        with self._lock:
            if (
                not force
                and self._last_refresh_result is not None
                and time.monotonic() - self._last_refresh_at < self.refresh_ttl_seconds
            ):
                result = copy.deepcopy(self._last_refresh_result)
                result["cache_hit"] = True
                result["request_duration_ms"] = round(
                    (time.perf_counter() - started) * 1000, 3
                )
                return result
            for topic in self.topics.values():
                checkpoints = self.checkpoints(topic)
                rows, next_offsets, error = self.reader(topic, checkpoints)
                inserted = 0
                if error is None:
                    with self._connect() as connection:
                        for event in rows:
                            partition = int(event["_kafka_partition"])
                            offset = int(event["_kafka_offset"])
                            event_id = event.get("id") or event.get("frame_id")
                            cursor = connection.execute(
                                """
                                INSERT OR IGNORE INTO dashboard_events
                                    (topic, partition_id, offset_id, event_id,
                                     payload_json, materialized_at)
                                VALUES (?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    topic,
                                    partition,
                                    offset,
                                    str(event_id) if event_id is not None else None,
                                    json.dumps(event, separators=(",", ":"), sort_keys=True),
                                    utc_now(),
                                ),
                            )
                            inserted += cursor.rowcount
                        for partition, next_offset in next_offsets.items():
                            connection.execute(
                                """
                                INSERT INTO dashboard_checkpoints
                                    (topic, partition_id, next_offset, updated_at)
                                VALUES (?, ?, ?, ?)
                                ON CONFLICT(topic, partition_id) DO UPDATE SET
                                    next_offset=excluded.next_offset,
                                    updated_at=excluded.updated_at
                                """,
                                (topic, int(partition), int(next_offset), utc_now()),
                            )
                sources[topic] = {
                    "read": len(rows),
                    "inserted": inserted,
                    "checkpoint_count": len(next_offsets),
                    "error": error,
                }
        result = {
            "sources": sources,
            "request_duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "refreshed_at": utc_now(),
            "cache_hit": False,
        }
        self._last_refresh_at = time.monotonic()
        self._last_refresh_result = copy.deepcopy(result)
        return result

    def checkpoints(self, topic: str) -> dict[int, int]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT partition_id, next_offset FROM dashboard_checkpoints WHERE topic=?",
                (topic,),
            ).fetchall()
        return {int(row["partition_id"]): int(row["next_offset"]) for row in rows}

    def events(
        self,
        topic: str,
        *,
        scenario_id: str | None = None,
        replay_only: bool = False,
    ) -> list[dict]:
        generation = self.generation(scenario_id) if scenario_id and replay_only else 0
        boundaries = (
            self._boundaries(scenario_id, generation, topic)
            if scenario_id and replay_only
            else {}
        )
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT partition_id, offset_id, payload_json
                FROM dashboard_events WHERE topic=?
                ORDER BY partition_id, offset_id
                """,
                (topic,),
            ).fetchall()
        events = []
        for row in rows:
            if int(row["offset_id"]) < boundaries.get(int(row["partition_id"]), 0):
                continue
            event = json.loads(row["payload_json"])
            if scenario_id is not None and event.get("scenario_id") != scenario_id:
                continue
            events.append(event)
        return events

    def reset_replay(self, scenario_id: str) -> int:
        """Advance the replay generation while retaining all materialized history."""
        with self._lock, self._connect() as connection:
            current = self._generation(connection, scenario_id)
            generation = current + 1
            connection.execute(
                """
                INSERT INTO dashboard_replay_state(scenario_id, generation, reset_at)
                VALUES (?, ?, ?)
                ON CONFLICT(scenario_id) DO UPDATE SET
                    generation=excluded.generation, reset_at=excluded.reset_at
                """,
                (scenario_id, generation, utc_now()),
            )
            checkpoints = connection.execute(
                "SELECT topic, partition_id, next_offset FROM dashboard_checkpoints"
            ).fetchall()
            for row in checkpoints:
                connection.execute(
                    """
                    INSERT INTO dashboard_replay_boundaries
                        (scenario_id, generation, topic, partition_id, minimum_offset)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        scenario_id,
                        generation,
                        row["topic"],
                        int(row["partition_id"]),
                        int(row["next_offset"]),
                    ),
                )
        return generation

    def generation(self, scenario_id: str | None) -> int:
        if not scenario_id:
            return 0
        with self._connect() as connection:
            return self._generation(connection, scenario_id)

    @staticmethod
    def _generation(connection, scenario_id: str) -> int:
        row = connection.execute(
            "SELECT generation FROM dashboard_replay_state WHERE scenario_id=?",
            (scenario_id,),
        ).fetchone()
        return int(row["generation"]) if row else 0

    def _boundaries(
        self, scenario_id: str, generation: int, topic: str
    ) -> dict[int, int]:
        if generation == 0:
            return {}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT partition_id, minimum_offset
                FROM dashboard_replay_boundaries
                WHERE scenario_id=? AND generation=? AND topic=?
                """,
                (scenario_id, generation, topic),
            ).fetchall()
        return {
            int(row["partition_id"]): int(row["minimum_offset"]) for row in rows
        }

    def record_review(
        self,
        *,
        scope: str,
        scenario_id: str | None,
        alert_id: str,
        action: str,
        responder_id: str,
    ) -> None:
        normalized_scenario = scenario_id or ""
        generation = self.generation(scenario_id) if scope == "scenario" else 0
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO dashboard_social_reviews
                    (scope, scenario_id, generation, alert_id, action,
                     responder_id, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(scope, scenario_id, generation, alert_id) DO UPDATE SET
                    action=excluded.action,
                    responder_id=excluded.responder_id,
                    reviewed_at=excluded.reviewed_at
                """,
                (
                    scope,
                    normalized_scenario,
                    generation,
                    alert_id,
                    action,
                    responder_id,
                    utc_now(),
                ),
            )

    def reviews(self, *, scope: str, scenario_id: str | None) -> list[dict]:
        normalized_scenario = scenario_id or ""
        generation = self.generation(scenario_id) if scope == "scenario" else 0
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT alert_id, action, responder_id, reviewed_at
                FROM dashboard_social_reviews
                WHERE scope=? AND scenario_id=? AND generation=?
                ORDER BY reviewed_at, alert_id
                """,
                (scope, normalized_scenario, generation),
            ).fetchall()
        return [dict(row) for row in rows]

    def record_processed_ids(
        self,
        scenario_id: str,
        observation_ids: list[str],
        telemetry_ids: list[str],
    ) -> None:
        generation = self.generation(scenario_id)
        with self._connect() as connection:
            for kind, identifiers in (
                ("observation", observation_ids),
                ("telemetry", telemetry_ids),
            ):
                for identifier in identifiers:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO dashboard_processed_evidence
                            (scenario_id, generation, evidence_type, evidence_id,
                             first_seen_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (scenario_id, generation, kind, str(identifier), utc_now()),
                    )

    def processed_ids(self, scenario_id: str) -> dict[str, list[str]]:
        generation = self.generation(scenario_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT evidence_type, evidence_id FROM dashboard_processed_evidence
                WHERE scenario_id=? AND generation=?
                ORDER BY evidence_type, evidence_id
                """,
                (scenario_id, generation),
            ).fetchall()
        result = {"observation": [], "telemetry": []}
        for row in rows:
            result.setdefault(row["evidence_type"], []).append(row["evidence_id"])
        return result
