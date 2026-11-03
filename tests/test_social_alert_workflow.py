from __future__ import annotations

import copy
from pathlib import Path

import pytest

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.nlp.alert_state import InvalidTransition
from core.nlp.social_alert_workflow import ContradictoryReport, SocialAlertWorkflow
from dashboard.social_alert_service import SocialAlertService
from dashboard import server as dashboard_server


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
    assert state["associations"] == [{
        "alert_id": "social-001",
        "gis_source_id": "osm-way-791288888",
        "graph_node_id": state["pending_alerts"][0]["report"]["node_id"],
    }]


def test_dashboard_adapter_reset_starts_a_clean_replay():
    service = SocialAlertService(
        GIS_PATH, clock=lambda: "2026-09-24T12:02:00+00:00"
    )
    event = _event()
    event.update({"_kafka_partition": 0, "_kafka_offset": 17})
    service.ingest_events([event])
    service.decide("social-001", "confirm", "responder-1")

    service.reset()
    assert service.state()["counts"]["confirmed"] == 0
    service.ingest_events([event])
    assert service.state()["counts"]["pending"] == 1


def test_dashboard_adapter_rebuilds_persisted_review_state():
    event = _event(alert_id="durable-review-001")
    event.update({"_kafka_partition": 0, "_kafka_offset": 42})
    service = SocialAlertService(
        GIS_PATH, clock=lambda: "2026-09-24T12:02:00+00:00"
    )
    service.rebuild(
        [event],
        [{
            "alert_id": "durable-review-001",
            "action": "confirm",
            "responder_id": "operator-a",
        }],
    )
    state = service.state()
    assert state["counts"]["confirmed"] == 1
    assert state["confirmed_alerts"][0]["responder_confirmation"][
        "responder_id"
    ] == "operator-a"


def test_dashboard_general_and_scenario_alert_state_are_isolated(monkeypatch):
    dashboard_server.topic_tails.clear()
    general = SocialAlertService(
        GIS_PATH, clock=lambda: "2026-09-24T12:02:00+00:00"
    )
    scenario = SocialAlertService(
        GIS_PATH, clock=lambda: "2026-09-24T12:02:00+00:00"
    )
    monkeypatch.setattr(dashboard_server, "social_alerts", general)
    monkeypatch.setattr(dashboard_server, "scenario_social_alerts", scenario)
    monkeypatch.setattr(
        dashboard_server,
        "_scenario_rows_visible_at_current_time",
        lambda rows: rows,
    )

    general_event = _event(
        alert_id="general-001",
        text="Flood water blocks Estuary Road; a vehicle needs assistance.",
    )
    general_event.update(
        {
            "scenario_id": "another-incident",
            "_kafka_partition": 0,
            "_kafka_offset": 30,
        }
    )
    scenario_event = _event(alert_id="scenario-001")
    scenario_event.update(
        {
            "scenario_id": "louisiana-east-flood-v1",
            "_kafka_partition": 0,
            "_kafka_offset": 31,
        }
    )
    rows = [general_event, scenario_event]
    class MaterializedStub:
        reviews_by_scope = {"scenario": [], "production": []}

        @staticmethod
        def refresh(**kwargs):
            return {"sources": {"social-posts": {"error": None}}}

        @staticmethod
        def events(topic, scenario_id=None, **kwargs):
            values = copy.deepcopy(rows)
            if scenario_id is not None:
                values = [row for row in values if row.get("scenario_id") == scenario_id]
            return values

        def reviews(self, scope, **kwargs):
            return list(self.reviews_by_scope[scope])

        def record_review(self, scope, alert_id, action, responder_id, **kwargs):
            self.reviews_by_scope[scope].append({
                "alert_id": alert_id,
                "action": action,
                "responder_id": responder_id,
            })

        @staticmethod
        def reset_replay(scenario_id):
            return 1

    monkeypatch.setattr(dashboard_server, "materialized_state", MaterializedStub())

    general_state = dashboard_server.social_alert_state(False)
    scenario_state = dashboard_server.social_alert_state(True)

    assert general_state["scope"] == "general"
    assert general_state["scenario_id"] is None
    assert [item["alert_id"] for item in general_state["pending_alerts"]] == [
        "general-001"
    ]
    assert scenario_state["scope"] == "scenario"
    assert scenario_state["scenario_id"] == "louisiana-east-flood-v1"
    assert [item["alert_id"] for item in scenario_state["pending_alerts"]] == [
        "scenario-001"
    ]
    assert all(
        item["alert_id"] != "general-001"
        for item in scenario_state["pending_alerts"]
    )

    confirmed = dashboard_server.review_social_alert(
        "scenario-001",
        "confirm",
        dashboard_server.ResponderDecision(responder_id="scenario-responder"),
    )
    rejected = dashboard_server.review_social_alert(
        "general-001",
        "reject",
        dashboard_server.ResponderDecision(responder_id="general-responder"),
    )
    assert confirmed["alert"]["status"] == "CONFIRMED"
    assert rejected["alert"]["status"] == "REPORTED_FALSE"
    assert scenario.state()["confirmed_alerts"][0]["alert_id"] == "scenario-001"
    assert general.state()["reported_false_alerts"][0]["alert_id"] == "general-001"
    scenario_everywhere = dashboard_server.social_alert_state(True)
    assert scenario_everywhere["counts"]["confirmed"] == 1
    assert scenario_everywhere["confirmed_alerts"][0]["responder_confirmation"] == (
        confirmed["alert"]["responder_confirmation"]
    )
    assert scenario_everywhere["associations"][0]["alert_id"] == "scenario-001"
    assert scenario_everywhere["graph_observations"][0]["alert_id"] == "scenario-001"

    class ScenarioRunnerStub:
        @staticmethod
        def control(action, speed):
            return {"state": "idle", "speed": speed or 10}

    monkeypatch.setattr(dashboard_server, "scenario_runner", ScenarioRunnerStub())
    dashboard_server.scenario_control(
        "reset", dashboard_server.ScenarioControlRequest()
    )

    assert scenario.state()["counts"]["confirmed"] == 0
    assert general.state()["counts"]["rejected"] == 1
