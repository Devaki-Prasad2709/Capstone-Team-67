from __future__ import annotations

import hashlib
from pathlib import Path

from dashboard.building_classification_service import BuildingClassificationService
from dashboard.map_api import map_config
from dashboard.operational_service import GIS_PATH, OperationalDashboardService
from dashboard.scenario_service import DashboardScenarioService


INDEX_HTML = Path(__file__).parents[1] / "dashboard" / "static" / "index.html"


def _fake_risk_report(snapshots, *args, **kwargs):
    graph = snapshots[-1]
    nodes = []
    total = len(graph)
    for rank, node_id in enumerate(reversed(list(graph.nodes)), start=1):
        nodes.append(
            {
                "tensor_row": node_id,
                "graph_node_id": node_id,
                "gis_source_id": graph.nodes[node_id]["gis_source_id"],
                "node_type": graph.nodes[node_id]["type"],
                "raw_logit": 0.0,
                "operational_score": 0.5,
                "failure_risk_score": (total - rank + 1) / total,
                "risk_rank": rank,
            }
        )
    return {
        "timeline": [{"snapshot_index": 0, "snapshot_timestamp": None, "nodes": nodes}],
        "risk_interpretation": "uncalibrated_relative_failure_risk_score",
    }


def test_operational_contract_orders_risk_and_preserves_frozen_gis(tmp_path, monkeypatch):
    import dashboard.operational_service as module

    monkeypatch.setattr(module, "predict_node_risk_report", _fake_risk_report)
    before = hashlib.sha256(GIS_PATH.read_bytes()).hexdigest()
    buildings = BuildingClassificationService(
        GIS_PATH, tmp_path / "classifications.sqlite3"
    )
    service = OperationalDashboardService(buildings)

    result = service.snapshot([], [])
    assert result["counts"] == {
        "structural_features": 21,
        "damage_observations": 0,
        "risk_nodes": 21,
    }
    assert len(result["top_five_risk_nodes"]) == 5
    assert result["highest_risk_node"]["risk_rank"] == 1
    assert result["layers"]["tgnn_risk"]["features"][20]["properties"]["highest_risk"]
    assert result["calibrated_probability"] is False
    assert result["provenance"]["structural_gis"]["kind"] == "real"
    assert result["provenance"]["telemetry"]["kind"] == "simulated"
    assert hashlib.sha256(GIS_PATH.read_bytes()).hexdigest() == before


def test_classification_refreshes_operational_structural_and_risk_layers(tmp_path, monkeypatch):
    import dashboard.operational_service as module

    monkeypatch.setattr(module, "predict_node_risk_report", _fake_risk_report)
    buildings = BuildingClassificationService(
        GIS_PATH, tmp_path / "classifications.sqlite3"
    )
    service = OperationalDashboardService(buildings)
    source_id = buildings.gis_data.buildings[0].id
    buildings.classify(source_id, "emergency shelter", "operator-a")

    result = service.snapshot([], [])
    structural = next(
        feature
        for feature in result["layers"]["structural_gis"]["features"]
        if feature["properties"].get("building_id") == source_id
    )
    risk = next(
        feature
        for feature in result["layers"]["tgnn_risk"]["features"]
        if feature["properties"]["gis_source_id"] == source_id
    )
    assert structural["properties"]["assigned_type"] == "emergency_shelter"
    assert risk["properties"]["assigned_building_type"] == "emergency_shelter"


def test_operational_replay_ignores_duplicate_telemetry_ids(tmp_path, monkeypatch):
    import dashboard.operational_service as module

    monkeypatch.setattr(module, "predict_node_risk_report", _fake_risk_report)
    buildings = BuildingClassificationService(
        GIS_PATH, tmp_path / "classifications.sqlite3"
    )
    service = OperationalDashboardService(buildings)
    target_id = next(iter(buildings.id_map))
    event = {
        "id": "telemetry-repeat-001",
        "scenario_id": "louisiana-east-flood-v1",
        "timestamp": 1_790_236_950.0,
        "target_id": target_id,
        "load": 0.72,
        "capacity": 0.8,
        "damage": 0.35,
        "event_type": "road_degradation",
    }

    result = service.snapshot([], [event, dict(event)])

    assert [item["id"] for item in result["telemetry_events"]] == [
        "telemetry-repeat-001"
    ]
    assert result["errors"] == []


def test_map_defaults_to_the_frozen_scenario_extent(monkeypatch):
    monkeypatch.delenv("MAP_SCENARIO_GEOJSON", raising=False)
    config = map_config()
    assert config["scenario"]["bbox"] == [
        -90.08553068272339,
        29.75831551754242,
        -90.07989924402005,
        29.763946956245753,
    ]
    assert config["scenario"]["center"][0] < -90.07


def test_dashboard_scenario_status_and_speed_do_not_require_kafka():
    service = DashboardScenarioService()
    assert service.status()["state"] == "idle"
    assert len(service.status()["upcoming_events"]) == 12
    changed = service.control("speed", 10)
    assert changed["speed"] == 10
    assert changed["state"] == "idle"


def test_operational_page_keeps_incident_information_in_required_order():
    html = INDEX_HTML.read_text(encoding="utf-8")
    ordered_ids = [
        'id="operationsMap"',
        'id="topRiskRows"',
        'id="opsPendingRows"',
        'id="opsSatelliteContent"',
        'id="timelineTrack"',
        'id="provenanceGrid"',
        'id="opsBuildingSelect"',
    ]
    positions = [html.index(item) for item in ordered_ids]
    assert positions == sorted(positions)
    assert 'data-map-layer="structural"' in html
    assert 'data-map-layer="damage"' in html
    assert 'data-map-layer="risk"' in html
    assert 'data-map-layer="hotspots"' in html
    assert 'id="operationsLoading"' in html
    assert 'id="operationsError"' in html
