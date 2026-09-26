from __future__ import annotations

import copy
import json
from pathlib import Path

import networkx as nx
import pytest

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.observation.observation_log import ObservationLog, ObservationRecord
from core.observation.state_update import (
    STATE_FIELDS,
    apply_observations,
    explain_graph_transition,
    recompute_node_state,
)
from core.observation.telemetry import apply_telemetry_event


ROOT = Path(__file__).parents[1]
GIS_PATH = ROOT / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"
TIMELINE_PATH = ROOT / "scenarios/louisiana_east_flood/timeline.json"


def _scenario_graph():
    data = load_gis(str(GIS_PATH))
    return (*build_graph_from_gis(data, seed=42), data)


def _observation(node_id, timestamp=100.0, confidence=0.9, class_name="Severe"):
    return ObservationRecord(
        observation_id=f"obs-{node_id}-{timestamp}",
        timestamp=timestamp,
        frame_id="image-001.jpg",
        track_id=None,
        source="drone",
        class_id=1,
        class_name=class_name,
        confidence=confidence,
        bbox=(1, 2, 30, 40),
        latitude=29.76,
        longitude=-90.08,
        working_x=0,
        working_y=0,
        building_id=None,
        node_id=node_id,
    )


def test_baseline_has_complete_state_contract_and_is_missing_not_fabricated():
    graph, _, _ = _scenario_graph()
    for _, node in graph.nodes(data=True):
        assert set(STATE_FIELDS) <= set(node)
        assert node["effective_capacity"] == node["capacity"]
        assert node["utilization"] == pytest.approx(0.35)
        assert node["stress"] == pytest.approx(0.35)
        assert node["status_label"] == "operational"
        assert node["freshness_status"] == "missing"
        assert node["last_observation_timestamp"] is None


def test_damage_observation_reduces_capacity_and_increases_stress_immutably():
    baseline, id_map, _ = _scenario_graph()
    frozen = copy.deepcopy(baseline)
    node_id = id_map["osm-way-791288888"]
    log = ObservationLog()
    log.add(_observation(node_id))

    snapshot = apply_observations(baseline, log, now=110.0)
    before, after = baseline.nodes[node_id], snapshot.nodes[node_id]
    assert after["damage"] > before["damage"]
    assert after["effective_capacity"] < before["effective_capacity"]
    assert after["stress"] > before["stress"]
    assert after["last_observation_timestamp"] == 100.0
    assert after["freshness_seconds"] == 10.0
    assert after["freshness_status"] == "current"
    assert after["state_timestamp"] == 110.0
    assert baseline.graph == frozen.graph
    assert dict(baseline.nodes(data=True)) == dict(frozen.nodes(data=True))
    assert list(baseline.edges(data=True)) == list(frozen.edges(data=True))


def test_degradation_then_explicit_recovery_reduces_damage_and_stress():
    baseline, id_map, _ = _scenario_graph()
    target = id_map["osm-way-791288888"]
    degraded = apply_telemetry_event(
        baseline,
        id_map,
        {
            "id": "telemetry-001",
            "timestamp": 150.0,
            "target_id": "osm-way-791288888",
            "load": 0.72,
            "capacity": 0.8,
            "damage": 0.35,
            "event_type": "road_degradation",
        },
    )
    recovered = apply_telemetry_event(
        degraded,
        id_map,
        {
            "id": "telemetry-002",
            "timestamp": 300.0,
            "target_id": "osm-way-791288888",
            "load": 0.44,
            "capacity": 0.8,
            "damage": 0.15,
            "event_type": "road_recovery",
        },
    )
    damaged, repaired = degraded.nodes[target], recovered.nodes[target]
    assert damaged["damage"] == 0.35
    assert damaged["status_label"] == "failed"
    assert repaired["damage"] == 0.15
    assert repaired["effective_capacity"] > damaged["effective_capacity"]
    assert repaired["stress"] < damaged["stress"]
    assert repaired["status_label"] == "operational"
    assert repaired["telemetry_timestamp"] == 300.0
    assert baseline.nodes[target]["damage"] == 0.0


def _cascade_graph():
    graph = nx.DiGraph(working_crs="EPSG:32615", snapshot_sequence=0)
    for node_id, node_type in enumerate(("power", "telecom", "social")):
        graph.add_node(
            node_id,
            gis_source_id=f"node-{node_id}",
            type=node_type,
            pos=(float(node_id), 0.0),
            load=0.2,
            capacity=1.0,
            damage=0.0,
            dependency_factor=1.0,
            last_observation_timestamp=None,
            freshness_seconds=None,
            freshness_status="missing",
        )
        recompute_node_state(graph.nodes[node_id])
    graph.add_edge(0, 1, edge_type=1, weight=1.0, delay=1)
    graph.add_edge(1, 2, edge_type=1, weight=1.0, delay=1)
    return graph


def test_dependency_failure_propagates_service_loss_not_physical_damage():
    baseline = _cascade_graph()
    failed = apply_telemetry_event(
        baseline,
        {"power": 0},
        {
            "id": "power-failure",
            "timestamp": 10.0,
            "target_id": "power",
            "load": 0.9,
            "capacity": 1.0,
            "damage": 0.8,
            "event_type": "power_degradation",
        },
    )
    assert failed.nodes[0]["status_label"] == "failed"
    for dependent in (1, 2):
        assert failed.nodes[dependent]["dependency_factor"] == 0.0
        assert failed.nodes[dependent]["effective_capacity"] == pytest.approx(1e-6)
        assert failed.nodes[dependent]["stress"] == 1.0
        assert failed.nodes[dependent]["status_label"] == "failed"
        assert failed.nodes[dependent]["damage"] == 0.0

    restored = apply_telemetry_event(
        failed,
        {"power": 0},
        {
            "id": "power-recovery",
            "timestamp": 20.0,
            "target_id": "power",
            "load": 0.2,
            "capacity": 1.0,
            "damage": 0.0,
            "event_type": "power_recovery",
        },
    )
    assert all(restored.nodes[node]["status_label"] == "operational" for node in (0, 1, 2))
    assert all(restored.nodes[node]["stress"] == pytest.approx(0.2) for node in (0, 1, 2))


def test_missing_future_stale_and_out_of_order_data_are_explicit():
    baseline, id_map, _ = _scenario_graph()
    target = id_map["osm-way-791288888"]
    missing = apply_observations(baseline, ObservationLog(), now=1000.0)
    assert missing.nodes[target]["freshness_status"] == "missing"
    assert missing.nodes[target]["damage"] == baseline.nodes[target]["damage"]

    log = ObservationLog()
    log.add(_observation(target, timestamp=100.0))
    stale = apply_observations(baseline, log, now=2000.0, stale_after_seconds=300.0)
    assert stale.nodes[target]["freshness_status"] == "stale"
    assert stale.nodes[target]["freshness_seconds"] == 1900.0

    future = ObservationLog()
    future.add(_observation(target, timestamp=5000.0))
    ignored = apply_observations(baseline, future, now=1000.0)
    assert ignored.nodes[target]["damage"] == 0.0
    assert ignored.nodes[target]["freshness_status"] == "missing"

    degraded = apply_telemetry_event(
        baseline,
        id_map,
        {
            "id": "newer",
            "timestamp": 200.0,
            "target_id": "osm-way-791288888",
            "load": 0.5,
            "capacity": 0.8,
            "damage": 0.2,
        },
    )
    with pytest.raises(ValueError, match="Stale telemetry"):
        apply_telemetry_event(
            degraded,
            id_map,
            {
                "id": "older",
                "timestamp": 199.0,
                "target_id": "osm-way-791288888",
                "load": 0.1,
                "capacity": 0.8,
                "damage": 0.0,
            },
        )
    replay = apply_telemetry_event(
        degraded,
        id_map,
        {
            "id": "newer",
            "timestamp": 200.0,
            "target_id": "osm-way-791288888",
            "load": 0.5,
            "capacity": 0.8,
            "damage": 0.2,
        },
    )
    assert dict(replay.nodes(data=True)) == dict(degraded.nodes(data=True))


def test_transition_explanation_reports_exact_field_deltas_and_no_topology_change():
    baseline, id_map, _ = _scenario_graph()
    updated = apply_telemetry_event(
        baseline,
        id_map,
        {
            "id": "telemetry-001",
            "timestamp": 150.0,
            "target_id": "osm-way-791288888",
            "load": 0.72,
            "capacity": 0.8,
            "damage": 0.35,
            "event_type": "road_degradation",
        },
    )
    audit = explain_graph_transition(
        baseline, updated, event_id="timeline-07-telemetry-degrade", event_type="telemetry"
    )
    target = next(
        change for change in audit["node_changes"]
        if change["gis_source_id"] == "osm-way-791288888"
    )
    assert audit["graph_changed"] is True
    assert audit["topology_changed"] is False
    assert {"load", "damage", "effective_capacity", "stress", "status_label"} <= set(
        target["changes"]
    )

    timeline = json.loads(TIMELINE_PATH.read_text(encoding="utf-8"))["events"]
    records = [
        explain_graph_transition(
            baseline,
            baseline,
            event_id=event["event_id"],
            event_type=event["name"],
        )
        for event in timeline
    ]
    assert len(records) == 12
    assert {record["event_id"] for record in records} == {
        event["event_id"] for event in timeline
    }
    assert all("graph_changed" in record and "node_changes" in record for record in records)
