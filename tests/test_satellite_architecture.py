"""Architectural regression tests for dashboard-only satellite evidence."""

from __future__ import annotations

import copy
import io
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from consumers.observation_consumer import initialize_state
from core.satellite.change_contract import build_change_result
from dashboard import server
from dashboard.social_alert_service import SocialAlertService
from tgnn.utils.helpers import MODEL_INPUT_FEATURE_ORDER, NODE_FEATURE_ORDER


ROOT = Path(__file__).resolve().parents[1]
GIS_PATH = ROOT / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"
RESULT_TOPIC = "satellite-change-results"


def _jpeg(value: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (value, value, value)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _event(phase: str, payload: bytes) -> dict:
    return {
        "image_id": f"{phase}.tif",
        "timestamp": 100.0 if phase == "pre" else 130.0,
        "scenario_timestamp": (
            "2026-09-24T12:00:00Z" if phase == "pre" else "2026-09-24T12:00:30Z"
        ),
        "scenario_event_id": f"satellite-{phase}",
        "source": "satellite",
        "transfer_mode": "test",
        "satellite_phase": phase,
        "tile_id": "2_23_44",
        "bbox": [-90.1, 29.7, -90.0, 29.8],
        "scenario_id": "louisiana-east-flood-v1",
        "input_origin": "real-spacenet8",
        "simulation_fields": ["scenario_timestamp"],
        "content_hash": phase,
        "size_bytes": len(payload),
        "payload": payload,
    }


def _all_keys(value) -> set[str]:
    if isinstance(value, dict):
        keys = set(value)
        for item in value.values():
            keys.update(_all_keys(item))
        return keys
    if isinstance(value, list):
        keys = set()
        for item in value:
            keys.update(_all_keys(item))
        return keys
    return set()


def test_result_topic_has_only_the_declared_producer_and_dashboard_consumer():
    """Fail if a new production Python module starts routing this result."""
    references = set()
    for path in ROOT.rglob("*.py"):
        relative = path.relative_to(ROOT).as_posix()
        if relative.startswith((".venv/", "tests/", "scripts/")):
            continue
        text = path.read_text(encoding="utf-8")
        if RESULT_TOPIC in text or "SATELLITE_CHANGE_TOPIC" in text:
            references.add(relative)

    assert references == {
        "common/topics.py",                    # canonical declaration
        "core/satellite/change_worker.py",     # sole producer
        "dashboard/server.py",                 # sole semantic runtime consumer
    }

    forbidden_roots = (
        ROOT / "consumers",
        ROOT / "core/graphs",
        ROOT / "core/integration",
        ROOT / "core/nlp",
        ROOT / "core/observation",
        ROOT / "tgnn",
    )
    forbidden_identifiers = (
        RESULT_TOPIC,
        "SATELLITE_CHANGE_TOPIC",
        "broad_area_change",
        "core.satellite",
    )
    violations = []
    for folder in forbidden_roots:
        for path in folder.rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if any(identifier in text for identifier in forbidden_identifiers):
                violations.append(path.relative_to(ROOT).as_posix())
    assert violations == []


def test_dashboard_routes_keep_satellite_graph_and_social_inputs_separate():
    topics = []

    def recent(topic, _limit):
        topics.append(topic)
        return [], None

    with (
        patch("dashboard.server.recent_topic_events", side_effect=recent),
        patch("dashboard.server.operational_dashboard.snapshot", return_value={}),
    ):
        server.operational_view()
    assert topics == ["ai-analysis-results", "infrastructure-telemetry"]

    topics.clear()
    with (
        patch("dashboard.server.recent_topic_events", side_effect=recent),
        patch("dashboard.server.social_alerts.ingest_events") as ingest,
        patch("dashboard.server.social_alerts.state", return_value={}),
    ):
        server.social_alert_state()
    assert topics == ["social-posts"]
    ingest.assert_called_once_with([])

    topics.clear()
    with (
        patch("dashboard.server.recent_topic_events", side_effect=recent),
        patch("dashboard.server.scenario_runner.status", return_value={
            "state": "completed",
            "simulation_timestamp": "2026-09-24T12:05:30Z",
        }),
    ):
        server.satellite_change_output()
    assert topics == [RESULT_TOPIC]


def test_building_satellite_evidence_cannot_mutate_graph_observations_or_hotspots():
    observation_state = initialize_state(
        str(GIS_PATH), allow_scenario_simulated_gps=True
    )
    social = SocialAlertService(GIS_PATH)
    graph_before = {
        "metadata": copy.deepcopy(observation_state.graph.graph),
        "nodes": copy.deepcopy(dict(observation_state.graph.nodes(data=True))),
        "edges": copy.deepcopy(list(observation_state.graph.edges(data=True))),
    }
    social_before = social.state()

    pre_payload, post_payload = _jpeg(80), _jpeg(180)
    result = build_change_result(
        _event("pre", pre_payload),
        _event("post", post_payload),
        lambda event: event["payload"],
        grid_size=4,
    )

    graph_after = {
        "metadata": copy.deepcopy(observation_state.graph.graph),
        "nodes": copy.deepcopy(dict(observation_state.graph.nodes(data=True))),
        "edges": copy.deepcopy(list(observation_state.graph.edges(data=True))),
    }
    assert graph_after == graph_before
    assert len(observation_state.observation_log) == 0
    assert social.state() == social_before
    assert social.state()["confirmed_hotspots"]["features"] == []
    assert social.state()["graph_observations"] == []

    assert result["data_type"] == "broad_area_change"
    assert result["tgnn_integration"] == "none"
    assert result["change"]["metadata"]["source"] == "universal_satellite_normalizer"
    assert result["change"]["metadata"]["crs"] == "EPSG:4326"
    assert result["change"]["type"] == "FeatureCollection"
    assert len(result["change"]["features"]) == 16

    forbidden_result_fields = {
        "node_id",
        "graph_node_id",
        "damage",
        "load",
        "capacity",
        "effective_capacity",
        "utilization",
        "stress",
        "failure_risk_score",
        "tgnn_feature",
    }
    assert _all_keys(result).isdisjoint(forbidden_result_fields)
    assert set(NODE_FEATURE_ORDER) == {
        "x_pos", "y_pos", "load", "capacity", "damage", "stress", "status"
    }
    assert set(MODEL_INPUT_FEATURE_ORDER) == {
        "x_pos", "y_pos", "load", "capacity", "damage", "stress"
    }
