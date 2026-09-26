"""
test_ingest.py -- proves ingest_ai_analysis_result() works against the
REAL ai-analysis-results contract shape (per the handoff doc), not just
hand-built ObservationRecords like the original pipeline_demo.py.

This is the direct test for handoff items #1-3 on your priority list:
    YOLO event -> tracking/geolocation -> building/node association
    -> Observation Log
(minus real tracking/telemetry, which still don't exist -- this proves
everything else in that chain works against the real event shape.)
"""

import time
import math
from pathlib import Path

from ai.computer_vision.contracts import result_event
from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.gis.building_association import build_building_lookup, SpatialIndex
from core.observation.geolocator import ManualOverrideGeoLocator
from core.observation.observation_log import ObservationLog
from core.observation.state_update import apply_observations
from core.observation.ingest import ingest_ai_analysis_result

# Resolve the fixture relative to this repo's `core` package, not the
# caller's working directory -- same convention as pipeline_demo.py.
# This test file lives at <repo_root>/tests/test_ingest.py, and the fixture
# lives at <repo_root>/core/gis/fixtures/demo_gis.geojson.
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "gis" / "fixtures" / "demo_gis.geojson"
)


def make_realistic_event(frame_id, class_name, confidence, source_timestamp):
    """
    Matches the EXACT contract shape from the handoff doc's inspected
    ai-analysis-results schema -- not a simplified version.
    """
    return result_event({
        "frame_id": frame_id,
        "source": "drone",
        "timestamp": source_timestamp,
        "object_key": f"drone/{frame_id}.jpg",
        "content_hash": "deadbeef",
        "canonical_image_id": frame_id,

    }, "analyzed", detections=[
            {
                "class_id": {"Slight": 0, "Severe": 1, "Debris": 2}[class_name],
                "class_name": class_name,
                "confidence": confidence,
                "bbox": [100, 100, 200, 200],
            }
        ])


def main():
    print("=" * 70)
    print("Setup: GIS -> G(0), building association")
    print("=" * 70)
    gis_data = load_gis(str(FIXTURE_PATH))
    G0, id_map = build_graph_from_gis(gis_data, seed=42)
    building_lookup = build_building_lookup(gis_data, id_map)
    spatial_index = SpatialIndex(gis_data, building_lookup, id_map)
    expected_node = id_map["B1"]
    assert isinstance(expected_node, int) and expected_node in G0

    geolocator = ManualOverrideGeoLocator()
    obs_log = ObservationLog()

    b1_lat, b1_lon = 12.7124, 77.6974  # inside Residential Block 1

    print()
    print("=" * 70)
    print("Ingesting 3 REAL-SHAPED ai-analysis-results events")
    print("=" * 70)
    now = time.time()
    events = [
        make_realistic_event("frame_001", "Slight", 0.82, now - 40 * 60),
        make_realistic_event("frame_002", "Severe", 0.76, now - 25 * 60),
        make_realistic_event("frame_003", "Debris", 0.88, now - 5 * 60),
    ]

    for event in events:
        assert event["model_name"] == "drone_detector_yolo26s"
        result = ingest_ai_analysis_result(
            event=event,
            geolocator=geolocator,
            spatial_index=spatial_index,
            obs_log=obs_log,
            drone_telemetry={"override_lat": b1_lat, "override_lon": b1_lon},
        )
        assert result.frame_id == event["frame_id"]
        assert result.detections_seen == result.detections_ingested == 1
        assert result.detections_skipped_no_node == 0
        assert result.touched_node_ids == [expected_node]
        record = obs_log.observations_for_node(expected_node)[-1]
        entry = event["detections"][0]
        assert (record.class_id, record.class_name) == (entry["class_id"], entry["class_name"])
        assert record.confidence == entry["confidence"]
        assert record.bbox == tuple(entry["bbox"])
        assert record.timestamp == event["source_timestamp"]
        assert record.building_id == "B1"
        assert record.matched_gis_source_id == "B1"
        assert record.graph_node_id == expected_node
        assert record.association_kind == "building"
        print(f"  {result.frame_id}: seen={result.detections_seen}, "
              f"ingested={result.detections_ingested}, "
              f"skipped={result.detections_skipped_no_node}, "
              f"touched_nodes={result.touched_node_ids}")

    print()
    print("=" * 70)
    print("Testing unknown class stays unknown and contributes zero damage")
    print("=" * 70)
    placeholder_event = make_realistic_event("frame_004", "Severe", 0.90, now - 2 * 60)
    placeholder_event["detections"][0]["class_name"] = "damage_class_2"
    placeholder_event["damage_classes"] = ["damage_class_2"]
    unknown_log = ObservationLog()
    result = ingest_ai_analysis_result(
        event=placeholder_event,
        geolocator=geolocator,
        spatial_index=spatial_index,
        obs_log=unknown_log,
        drone_telemetry={"override_lat": b1_lat, "override_lon": b1_lon},
    )
    assert result.detections_seen == result.detections_ingested == 1
    assert result.detections_skipped_no_node == 0
    assert result.touched_node_ids == [expected_node]
    unknown_record = unknown_log.observations_for_node(expected_node)[0]
    assert unknown_record.class_name == "damage_class_2"
    assert unknown_log.aggregate_damage(expected_node, now=now) == 0.0
    print("  PASS: damage_class_2 preserved; aggregated damage = 0.0")

    print()
    print("=" * 70)
    print("Testing a non-analyzed (failed) frame is correctly ignored")
    print("=" * 70)
    failed_event = make_realistic_event("frame_005", "Severe", 0.95, now)
    failed_event["status"] = "skipped"
    failed_event["reason"] = "blurry_frame"
    records_before = obs_log.observations_for_node(expected_node)
    result = ingest_ai_analysis_result(
        event=failed_event,
        geolocator=geolocator,
        spatial_index=spatial_index,
        obs_log=obs_log,
        drone_telemetry={"override_lat": b1_lat, "override_lon": b1_lon},
    )
    print(f"  frame_005 (status=skipped): ingested={result.detections_ingested} (should be 0)")
    assert result.detections_seen == 1
    assert result.detections_ingested == 0
    assert result.touched_node_ids == []
    assert obs_log.observations_for_node(expected_node) == records_before
    assert len(records_before) == 3
    assert obs_log.all_observed_node_ids() == [expected_node]

    print()
    print("=" * 70)
    print("Applying full observation log to graph -> G(t)")
    print("=" * 70)
    Gt = apply_observations(G0, obs_log, now=now)
    # Independent expected value: Debris is the strongest, freshest evidence.
    expected_damage = 0.88 * math.exp(-300 / (6 * 3600))
    assert math.isclose(obs_log.aggregate_damage(expected_node, now=now), expected_damage)
    assert math.isclose(Gt.nodes[expected_node]["damage"], expected_damage)
    baseline = G0.nodes[expected_node]
    expected_stress = min(1.0, baseline["load"] / (baseline["capacity"] * (1 - expected_damage) ** 1.5))
    assert math.isclose(Gt.nodes[expected_node]["stress"], expected_stress)
    assert baseline["damage"] == 0.0
    assert math.isclose(baseline["stress"], 0.35)
    assert dict(Gt.edges) == dict(G0.edges)
    assert set(Gt.nodes) == set(G0.nodes)
    for node_id in G0.nodes:
        if node_id != expected_node:
            assert Gt.nodes[node_id] == G0.nodes[node_id]
    for node_id in obs_log.all_observed_node_ids():
        before = G0.nodes[node_id]
        after = Gt.nodes[node_id]
        print(f"  node {node_id} ({after['type']}): "
              f"damage {before['damage']:.3f} -> {after['damage']:.3f}, "
              f"stress {before['stress']:.3f} -> {after['stress']:.3f}")
        print(f"    total observations logged for this node: "
              f"{len(obs_log.observations_for_node(node_id))}")

    print()
    print("ALL INGEST TESTS PASSED")


if __name__ == "__main__":
    main()
