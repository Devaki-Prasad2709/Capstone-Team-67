from __future__ import annotations

import copy
from pathlib import Path

from dashboard.materialized_state import DashboardMaterializedState


TOPICS = {
    "ai": "ai-analysis-results",
    "telemetry": "infrastructure-telemetry",
    "social": "social-posts",
    "satellite": "satellite-change-results",
}


class IncrementalSource:
    def __init__(self):
        self.rows = {topic: [] for topic in TOPICS.values()}

    def append(self, topic, payload, partition=0):
        existing = [
            row for row in self.rows[topic]
            if row["_kafka_partition"] == partition
        ]
        event = copy.deepcopy(payload)
        event["_kafka_partition"] = partition
        event["_kafka_offset"] = len(existing)
        self.rows[topic].append(event)

    def read(self, topic, checkpoints):
        rows = [
            copy.deepcopy(row)
            for row in self.rows[topic]
            if row["_kafka_offset"] >= checkpoints.get(row["_kafka_partition"], 0)
        ]
        partitions = {row["_kafka_partition"] for row in self.rows[topic]} or {0}
        next_offsets = {
            partition: 1 + max(
                (
                    row["_kafka_offset"]
                    for row in self.rows[topic]
                    if row["_kafka_partition"] == partition
                ),
                default=-1,
            )
            for partition in partitions
        }
        return rows, next_offsets, None


def populated_source():
    source = IncrementalSource()
    common = {
        "scenario_id": "scenario-1",
        "scenario_timestamp": "2026-01-01T00:05:00Z",
    }
    for index in range(250):
        source.append(TOPICS["ai"], {**common, "frame_id": f"frame-{index}"})
    for index in range(150):
        source.append(
            TOPICS["telemetry"], {**common, "id": f"telemetry-{index}"}
        )
    source.append(TOPICS["social"], {**common, "id": "social-1"})
    return source


def test_materialization_exceeds_old_tail_limits_and_survives_restart(tmp_path):
    source = populated_source()
    database = tmp_path / "projection.sqlite3"
    state = DashboardMaterializedState(
        source.read, TOPICS, database, refresh_ttl_seconds=0
    )

    first = state.refresh(force=True)
    second = state.refresh(force=True)

    assert len(state.events(TOPICS["ai"], scenario_id="scenario-1")) == 250
    assert len(state.events(TOPICS["telemetry"], scenario_id="scenario-1")) == 150
    assert first["sources"][TOPICS["ai"]]["inserted"] == 250
    assert second["sources"][TOPICS["ai"]]["inserted"] == 0
    assert state.checkpoints(TOPICS["ai"]) == {0: 250}

    restarted = DashboardMaterializedState(
        source.read, TOPICS, database, refresh_ttl_seconds=0
    )
    assert len(restarted.events(TOPICS["ai"], scenario_id="scenario-1")) == 250
    assert len(restarted.events(TOPICS["telemetry"], scenario_id="scenario-1")) == 150


def test_replay_reset_retains_production_history_but_starts_new_generation(tmp_path):
    source = populated_source()
    state = DashboardMaterializedState(
        source.read, TOPICS, tmp_path / "projection.sqlite3", refresh_ttl_seconds=0
    )
    state.refresh(force=True)
    state.record_review(
        scope="scenario",
        scenario_id="scenario-1",
        alert_id="social-1",
        action="confirm",
        responder_id="operator-a",
    )
    assert len(state.reviews(scope="scenario", scenario_id="scenario-1")) == 1

    generation = state.reset_replay("scenario-1")
    assert generation == 1
    assert state.reviews(scope="scenario", scenario_id="scenario-1") == []
    assert len(state.events(TOPICS["ai"], scenario_id="scenario-1")) == 250
    assert state.events(
        TOPICS["ai"], scenario_id="scenario-1", replay_only=True
    ) == []

    source.append(
        TOPICS["ai"],
        {
            "scenario_id": "scenario-1",
            "scenario_timestamp": "2026-01-01T00:06:00Z",
            "frame_id": "frame-new-generation",
        },
    )
    state.refresh(force=True)
    replay = state.events(
        TOPICS["ai"], scenario_id="scenario-1", replay_only=True
    )
    assert [row["frame_id"] for row in replay] == ["frame-new-generation"]
    assert len(state.events(TOPICS["ai"], scenario_id="scenario-1")) == 251


def test_reviews_and_processed_ids_are_durable_and_idempotent(tmp_path):
    database = tmp_path / "projection.sqlite3"
    source = populated_source()
    state = DashboardMaterializedState(source.read, TOPICS, database)
    state.refresh(force=True)
    state.record_review(
        scope="scenario",
        scenario_id="scenario-1",
        alert_id="social-1",
        action="reject",
        responder_id="operator-b",
    )
    state.record_processed_ids(
        "scenario-1", ["observation-1", "observation-1"], ["telemetry-1"]
    )

    restarted = DashboardMaterializedState(source.read, TOPICS, database)
    assert restarted.reviews(scope="scenario", scenario_id="scenario-1")[0][
        "action"
    ] == "reject"
    assert restarted.processed_ids("scenario-1") == {
        "observation": ["observation-1"],
        "telemetry": ["telemetry-1"],
    }


def test_materialization_is_local_read_model_not_a_pipeline_producer():
    root = Path(__file__).parents[1]
    source = "\n".join(
        (root / path).read_text(encoding="utf-8")
        for path in (
            "dashboard/materialized_state.py",
            "dashboard/projection_service.py",
        )
    )
    assert "KafkaProducer" not in source
    assert ".send(" not in source
    assert "process_message" not in source
    assert "predict_node_risk" not in source
