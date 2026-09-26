from __future__ import annotations

import copy
from pathlib import Path

import pytest

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.nlp.alert_state import InvalidTransition
from core.nlp.social_alert_workflow import ContradictoryReport, SocialAlertWorkflow
from dashboard.social_alert_service import SocialAlertService


GIS_PATH = (
    Path(__file__).parents[1]
    / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"
)


def _event(alert_id="social-001", text=None):
    return {
        "id": alert_id,
        "text": text or "Flood water blocks Estuary Road; people are stranded nearby.",
        "hazard": "flood",
        "event": "louisiana-east-flood-v1",
        "timestamp": 1790251320.0,
        "source": "twitter",
        "data_type": "text",
        "gps": {"longitude": -90.08320, "latitude": 29.76330},
        "target_ids": ["osm-way-791288888", "osm-way-1064972993"],
    }


@pytest.fixture
def workflow():
    data = load_gis(str(GIS_PATH))
    graph, id_map = build_graph_from_gis(data, seed=42)
    instance = SocialAlertWorkflow(
        data, graph, id_map, clock=lambda: "2026-09-24T12:02:00+00:00"
    )
    return instance, copy.deepcopy(graph), id_map


def test_incoming_then_pending_does_not_create_confirmed_state(workflow):
    instance, original_graph, _ = workflow
    outcome = instance.ingest_event(_event())
    state = instance.dashboard_state()

    assert outcome.outcome == "accepted"
    assert outcome.status == "PENDING_REVIEW"
    alert = state["pending_alerts"][0]
    assert [step["to_status"] for step in alert["history"]] == [
        "INCOMING",
        "PENDING_REVIEW",
    ]
    assert state["confirmed_hotspots"]["features"] == []
    assert state["graph_observations"] == []
    assert dict(instance.graph.nodes(data=True)) == dict(original_graph.nodes(data=True))


def test_confirmation_creates_orange_hotspot_and_exact_graph_observation(workflow):
    instance, original_graph, id_map = workflow
    instance.ingest_event(_event())
    confirmed = instance.decide("social-001", "confirm", responder_id="responder-1")
    state = instance.dashboard_state()

    assert confirmed["status"] == "CONFIRMED"
    assert state["pending_alerts"] == []
    hotspot = state["confirmed_hotspots"]["features"][0]
    assert hotspot["properties"]["marker_color"] == "orange"
    assert hotspot["properties"]["gis_source_id"] == "osm-way-791288888"
    observation = state["graph_observations"][0]
    assert observation["alert_id"] == "social-001"
    assert observation["gis_source_id"] == "osm-way-791288888"
    assert observation["graph_node_id"] == id_map["osm-way-791288888"]
    assert observation["affects_structural_damage"] is False
    assert observation["tgnn_feature"] is False
    assert dict(instance.graph.nodes(data=True)) == dict(original_graph.nodes(data=True))


def test_rejection_is_auditable_without_hotspot_or_graph_observation(workflow):
    instance, _, _ = workflow
    instance.ingest_event(_event())
    rejected = instance.decide("social-001", "reject", responder_id="responder-2")
    state = instance.dashboard_state()

    assert rejected["status"] == "REPORTED_FALSE"
    assert len(rejected["history"]) == 3
    assert state["reported_false_alerts"][0]["original_event"]["text"] == _event()["text"]
    assert state["confirmed_hotspots"]["features"] == []
    assert state["graph_observations"] == []


def test_exact_and_content_duplicates_are_explicit_and_idempotent(workflow):
    instance, _, _ = workflow
    first = instance.ingest_event(_event())
    exact = instance.ingest_event(_event())
    same_content = instance.ingest_event(_event(alert_id="social-copy"))

    assert first.outcome == "accepted"
    assert exact.outcome == same_content.outcome == "duplicate"
    assert exact.canonical_alert_id == same_content.canonical_alert_id == "social-001"
    assert len(instance.store.list_alerts()) == 1


def test_contradictory_source_and_decisions_are_rejected_without_overwrite(workflow):
    instance, _, _ = workflow
    instance.ingest_event(_event())
    with pytest.raises(ContradictoryReport, match="conflicts"):
        instance.ingest_event(_event(text="Everything is fine on Estuary Road."))

    instance.decide("social-001", "confirm", responder_id="responder-1")
    confirmed = instance.store.get_alert("social-001")
    with pytest.raises(InvalidTransition):
        instance.decide("social-001", "reject", responder_id="responder-2")
    with pytest.raises(InvalidTransition):
        instance.decide("social-001", "confirm", responder_id="responder-1")
    assert instance.store.get_alert("social-001") == confirmed
    assert len(instance.store.graph_observations()) == 1


@pytest.mark.parametrize(
    "gps",
    [
        {"longitude": float("nan"), "latitude": 29.7},
        {"longitude": -90.0, "latitude": 91.0},
        "not-an-object",
    ],
)
def test_invalid_coordinates_never_enter_review(gps, workflow):
    instance, _, _ = workflow
    event = _event()
    event["gps"] = gps
    with pytest.raises(ValueError, match="gps"):
        instance.ingest_event(event)
    assert instance.store.list_alerts() == []


def test_dashboard_adapter_processes_each_kafka_offset_once():
    service = SocialAlertService(
        GIS_PATH, clock=lambda: "2026-09-24T12:02:00+00:00"
    )
    event = _event()
    event.update({"_kafka_partition": 0, "_kafka_offset": 17})
    service.ingest_events([event])
    service.ingest_events([event])
    state = service.state()
    assert state["counts"]["pending"] == 1
    assert len(state["ingest_outcomes"]) == 1
    assert state["ingest_errors"] == []
