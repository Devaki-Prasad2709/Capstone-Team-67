from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from core.gis.building_association import SpatialIndex, build_building_lookup
from core.gis.building_classification import (
    BuildingClassificationStore,
    apply_building_classifications,
)
from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.overlay.building_overlay import build_building_overlay
from dashboard.building_classification_service import BuildingClassificationService


SCENARIO_GIS = (
    Path(__file__).parents[1]
    / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _scenario(tmp_path):
    gis_data = load_gis(str(SCENARIO_GIS))
    graph, id_map = build_graph_from_gis(gis_data, seed=42)
    ids = {building.id for building in gis_data.buildings}
    store = BuildingClassificationStore(
        tmp_path / "building_classifications.sqlite3",
        building_source_ids=ids,
    )
    return gis_data, graph, id_map, store


def test_assignment_survives_restart_and_preserves_revision_history(tmp_path):
    gis_data, _, _, store = _scenario(tmp_path)
    source_id = gis_data.buildings[0].id
    raw_hash = _sha256(SCENARIO_GIS)

    first = store.assign(
        source_id,
        "Emergency Shelter",
        operator="responder-17",
        timestamp="2026-09-24T12:02:00Z",
        notes="Primary evacuation point",
    )
    assert first == {
        "revision_id": 1,
        "building_source_id": source_id,
        "revision_number": 1,
        "assigned_type": "emergency_shelter",
        "operator": "responder-17",
        "timestamp": "2026-09-24T12:02:00Z",
        "notes": "Primary evacuation point",
    }

    restarted = BuildingClassificationStore(
        store.database_path,
        building_source_ids={building.id for building in gis_data.buildings},
    )
    assert restarted.get(source_id) == first

    second = restarted.assign(
        source_id,
        "field hospital",
        operator="incident-commander",
        timestamp="2026-09-24T12:08:00+00:00",
    )
    assert second["revision_number"] == 2
    assert restarted.get(source_id)["assigned_type"] == "field_hospital"
    assert [item["revision_number"] for item in restarted.history(source_id)] == [1, 2]
    assert _sha256(SCENARIO_GIS) == raw_hash


def test_unknown_building_and_invalid_audit_fields_are_rejected(tmp_path):
    _, _, _, store = _scenario(tmp_path)
    with pytest.raises(KeyError, match="Unknown GIS building"):
        store.assign("not-in-snapshot", "hospital", operator="responder")
    with pytest.raises(ValueError, match="operator"):
        store.assign(next(iter(store._known_ids)), "hospital", operator="  ")
    with pytest.raises(ValueError, match="timestamp"):
        store.assign(
            next(iter(store._known_ids)),
            "hospital",
            operator="responder",
            timestamp="not-a-date",
        )


def test_classification_is_available_through_gis_graph_and_overlay(tmp_path):
    gis_data, graph, id_map, store = _scenario(tmp_path)
    building = gis_data.buildings[0]
    source_id = building.id
    record = store.assign(
        source_id,
        "supply depot",
        operator="responder-4",
        timestamp="2026-09-24T12:03:00Z",
        notes="Water and medical supplies",
    )

    lookup = build_building_lookup(gis_data, id_map, store)
    index = SpatialIndex(gis_data, lookup, id_map, store)
    association = index.resolve(building.geometry.centroid.x, building.geometry.centroid.y)
    assert association["gis_source_id"] == source_id
    assert association["graph_node_id"] == id_map[source_id]
    assert association["building_classification"] == record

    enriched = apply_building_classifications(graph, store)
    assert "building_classification" not in graph.nodes[id_map[source_id]]
    assert enriched.nodes[id_map[source_id]]["building_classification"] == record
    assert enriched.nodes[id_map[source_id]]["assigned_building_type"] == "supply_depot"

    overlay = build_building_overlay(gis_data, id_map, store)
    feature = next(item for item in overlay["features"] if item["id"] == source_id)
    assert feature["properties"]["classification"] == record
    assert feature["properties"]["graph_node_id"] == id_map[source_id]


def test_dashboard_service_reads_persisted_classification_after_restart(tmp_path):
    database = tmp_path / "dashboard-classifications.sqlite3"
    first = BuildingClassificationService(SCENARIO_GIS, database)
    source_id = first.gis_data.buildings[0].id
    first.classify(source_id, "command center", "operator-a", "Initial assignment")

    restarted = BuildingClassificationService(SCENARIO_GIS, database)
    detail = restarted.get_building(source_id)
    assert detail["classification"]["assigned_type"] == "command_center"
    assert detail["revision_history"][0]["operator"] == "operator-a"
    assert detail["graph_node"]["assigned_building_type"] == "command_center"


def test_dashboard_api_functions_expose_current_and_historical_data(tmp_path, monkeypatch):
    import dashboard.server as server

    service = BuildingClassificationService(
        SCENARIO_GIS, tmp_path / "api-classifications.sqlite3"
    )
    monkeypatch.setattr(server, "building_classifications", service)
    source_id = service.gis_data.buildings[0].id

    saved = server.classify_building(
        source_id,
        server.BuildingClassificationRequest(
            assigned_type="emergency shelter",
            operator="dashboard-responder",
            notes="Selected in operations dashboard",
        ),
    )
    assert saved["ok"] is True
    assert saved["classification"]["building_source_id"] == source_id

    detail = server.building(source_id)
    assert detail["classification"]["assigned_type"] == "emergency_shelter"
    assert len(detail["revision_history"]) == 1
    listing = server.buildings()
    assert listing["classified_count"] == 1
    assert listing["raw_gis_mutated"] is False
