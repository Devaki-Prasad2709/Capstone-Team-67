from __future__ import annotations

import copy
import json
import math
from pathlib import Path

import pytest
from shapely.geometry import LineString, Polygon

from ai.computer_vision.contracts import result_event
from consumers.observation_consumer import initialize_state, process_message
from core.gis.building_association import (
    NEAREST_ROAD_FALLBACK_RADIUS_M,
    SpatialIndex,
    build_building_lookup,
)
from core.gis.gis_loader import Building, GISData, RoadSegment
from core.graphs.gis_graph_builder import build_graph_from_gis


SCENARIO_GIS = Path(__file__).parents[1] / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"


def _index(*, roads=(), buildings=()):
    data = GISData(
        source_label="ASSOCIATION_TEST",
        road_segments=list(roads),
        buildings=list(buildings),
        working_crs="EPSG:3857",
    )
    graph, id_map = build_graph_from_gis(data, seed=42)
    return graph, id_map, SpatialIndex(data, build_building_lookup(data, id_map), id_map)


def _road(source_id: str, coordinates):
    return RoadSegment(source_id, source_id, LineString(coordinates), lanes=2)


def _building(source_id: str, coordinates):
    return Building(source_id, source_id, Polygon(coordinates))


def test_inside_building_maps_source_id_to_its_exact_graph_node():
    building = _building("building-1", [(0, 0), (10, 0), (10, 10), (0, 10)])
    road = _road("road-1", [(-20, -5), (20, -5)])
    graph, id_map, index = _index(roads=[road], buildings=[building])

    match = index.resolve(5, 5)
    assert match == {
        "association_kind": "building",
        "gis_source_id": "building-1",
        "graph_node_id": id_map["building-1"],
        "building_id": "building-1",
        "node_id": id_map["building-1"],
        "distance_m": 0.0,
        "candidate_count": 1,
    }
    assert graph.nodes[match["graph_node_id"]]["gis_source_id"] == "building-1"


def test_near_road_maps_to_road_source_and_node():
    graph, id_map, index = _index(roads=[_road("road-1", [(0, 0), (100, 0)])])
    match = index.resolve(50, 12)
    assert match["association_kind"] == "road"
    assert match["gis_source_id"] == "road-1"
    assert match["graph_node_id"] == id_map["road-1"]
    assert match["distance_m"] == pytest.approx(12)
    assert graph.nodes[match["node_id"]]["gis_source_id"] == "road-1"


def test_between_equal_road_candidates_uses_stable_source_id_tie_break():
    _, id_map, index = _index(roads=[
        _road("road-z", [(-20, -10), (20, -10)]),
        _road("road-a", [(-20, 10), (20, 10)]),
    ])
    match = index.resolve(0, 0)
    assert match["candidate_count"] == 2
    assert match["gis_source_id"] == "road-a"
    assert match["node_id"] == id_map["road-a"]


def test_no_candidate_found_returns_explicit_empty_association():
    _, _, index = _index(roads=[_road("road-1", [(0, 0), (10, 0)])])
    match = index.resolve(1000, 1000)
    assert match["association_kind"] is None
    assert match["gis_source_id"] is None
    assert match["graph_node_id"] is None
    assert match["node_id"] is None


@pytest.mark.parametrize("coordinates", [(math.nan, 0), (0, math.inf), (True, 0)])
def test_invalid_projected_coordinates_are_rejected(coordinates):
    _, _, index = _index(roads=[_road("road-1", [(0, 0), (10, 0)])])
    with pytest.raises(ValueError, match="finite projected"):
        index.resolve(*coordinates)


def test_coordinate_boundaries_are_defined_and_inclusive():
    building = _building("building-1", [(0, 0), (10, 0), (10, 10), (0, 10)])
    _, id_map, index = _index(
        roads=[_road("road-1", [(0, -200), (0, 200)])], buildings=[building]
    )
    boundary = index.resolve(0, 5)
    assert boundary["association_kind"] == "building"
    assert boundary["node_id"] == id_map["building-1"]

    _, _, road_only = _index(roads=[_road("road-1", [(0, 0), (100, 0)])])
    assert road_only.resolve(50, NEAREST_ROAD_FALLBACK_RADIUS_M)["association_kind"] == "road"
    assert road_only.resolve(50, NEAREST_ROAD_FALLBACK_RADIUS_M + 0.001)["node_id"] is None


def _scenario_event(gps=None):
    return result_event(
        {
            "frame_id": "6_270.jpg",
            "asset_id": "drone-severe-001",
            "source": "drone",
            "timestamp": 1790251290.0,
            "transfer_mode": "object_storage",
            "object_key": "drone/67/6_270.jpg",
            "content_hash": "67" * 32,
            "scenario_id": "louisiana-east-flood-v1",
            "scenario_event_id": "drone-002",
            "scenario_timestamp": "2026-09-24T12:01:30Z",
            "gps": gps or {"longitude": -90.08543330768804, "latitude": 29.763329185970875},
            "target_id": "osm-way-1064972993",
            "simulation_fields": ["scenario_timestamp", "gps", "target_id"],
        },
        "analyzed",
        [{"class_id": 1, "class_name": "Severe", "confidence": 0.8, "bbox": [1, 2, 30, 40]}],
        model_checkpoint_sha256="78" * 32,
    )


def test_image_to_exact_graph_node_trace_and_duplicate_idempotency():
    state = initialize_state(str(SCENARIO_GIS), allow_scenario_simulated_gps=True)
    event = _scenario_event()
    payload = json.dumps(event).encode()

    first = process_message(payload, state)
    assert first.detections_ingested == 1
    assert first.detections_duplicate == 0
    observation_id = first.accepted_observation_ids[0]
    record = state.observation_log.get(observation_id)
    assert record is not None
    assert record.image_reference["frame_id"] == "6_270.jpg"
    assert record.source_content_hash == event["content_hash"]
    assert record.matched_gis_source_id == "osm-way-1064972993"
    assert record.node_id == 19
    assert state.graph.nodes[record.node_id]["gis_source_id"] == record.matched_gis_source_id
    assert record.declared_target_id == record.matched_gis_source_id

    graph_before = copy.deepcopy(state.graph)
    second = process_message(payload, state)
    assert second.detections_ingested == 0
    assert second.detections_duplicate == 1
    assert len(state.observation_log) == 1
    assert dict(state.graph.nodes) == dict(graph_before.nodes)
    assert dict(state.graph.edges) == dict(graph_before.edges)


def test_invalid_wgs84_coordinates_never_reach_the_log():
    state = initialize_state(str(SCENARIO_GIS), allow_scenario_simulated_gps=True)
    event = _scenario_event({"longitude": -90.08, "latitude": 91.0})
    with pytest.raises(ValueError, match="WGS84"):
        process_message(json.dumps(event).encode(), state)
    assert len(state.observation_log) == 0
