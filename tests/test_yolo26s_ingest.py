import cv2
import time
from pathlib import Path

from ai.computer_vision.detector import DamageDetector
from ai.computer_vision.contracts import result_event

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.gis.building_association import build_building_lookup, SpatialIndex
from core.observation.geolocator import ManualOverrideGeoLocator
from core.observation.observation_log import ObservationLog
from core.observation.state_update import apply_observations
from core.observation.ingest import ingest_ai_analysis_result


ROOT = Path(__file__).resolve().parents[1]

FIXTURE_PATH = (
    ROOT
    / "core"
    / "gis"
    / "fixtures"
    / "demo_gis.geojson"
)

MODEL = (
    ROOT
    / "ai"
    / "computer_vision"
    / "artifacts"
    / "drone_detector"
    / "weights"
    / "best.pt"
)

IMAGE = Path(
    r"E:\Capstone-Team-67\cv\data\isbda\images\val\7_1080.jpg"
)


print("=" * 70)
print("YOLO26s -> ai-analysis-results -> ingest.py -> G(t)")
print("=" * 70)


# ============================================================
# STEP 1: GIS -> G(0)
# ============================================================

gis_data = load_gis(str(FIXTURE_PATH))

G0, id_map = build_graph_from_gis(
    gis_data,
    seed=42,
)

print(
    f"\nG(0): "
    f"{G0.number_of_nodes()} nodes, "
    f"{G0.number_of_edges()} edges"
)


# ============================================================
# STEP 2: GIS building/node association
# ============================================================

building_lookup = build_building_lookup(
    gis_data,
    id_map,
)

spatial_index = SpatialIndex(
    gis_data,
    building_lookup,
    id_map,
)


# ============================================================
# STEP 3: Test-only geolocation
# ============================================================

geolocator = ManualOverrideGeoLocator()
obs_log = ObservationLog()


# ============================================================
# STEP 4: REAL YOLO26s inference
# ============================================================

detector = DamageDetector(
    MODEL,
    device="0",
)

image = cv2.imread(str(IMAGE))

if image is None:
    raise RuntimeError(
        f"Could not read image: {IMAGE}"
    )

detections = detector.predict([image])[0]

print(f"\nYOLO detections: {len(detections)}")

for detection in detections:
    print(
        f"  {detection['class_name']} | "
        f"confidence={detection['confidence']:.6f} | "
        f"bbox={detection['bbox']}"
    )


# ============================================================
# STEP 5: Build ai-analysis-results event
# ============================================================

event_timestamp = time.time()

event = result_event(
    {
        "frame_id": "yolo26s_integration_001",
        "source": "drone",
        "timestamp": event_timestamp,
        "object_key": str(IMAGE),
        "content_hash": "integration_test",
        "canonical_image_id": "yolo26s_test_001",
    },
    "analyzed",
    detections=detections,
)

print(
    f"\nContract: "
    f"status={event['status']} | "
    f"model={event['model_name']} | "
    f"detections={event['detection_count']}"
)


# ============================================================
# STEP 6: Ingest into ObservationLog
# ============================================================

result = ingest_ai_analysis_result(
    event,
    geolocator,
    spatial_index,
    obs_log,
    drone_telemetry={
        "override_lat": 12.7106,
        "override_lon": 77.6949,
    },
)

print(
    f"Ingest result: "
    f"seen={result.detections_seen} | "
    f"ingested={result.detections_ingested} | "
    f"skipped={result.detections_skipped_no_node} | "
    f"nodes={result.touched_node_ids}"
)


# ============================================================
# STEP 7: Verify ObservationLog
# ============================================================

print("\nObservationLog contents:")

for node_id in sorted(set(result.touched_node_ids)):

    records = obs_log._by_node.get(
        node_id,
        [],
    )

    print(
        f"  node={node_id} | "
        f"records={len(records)}"
    )

    for record in records:
        print(
            f"    class={record.class_name} | "
            f"confidence={record.confidence:.6f} | "
            f"age={time.time() - record.timestamp:.2f}s"
        )


# ============================================================
# STEP 8: Aggregate observation damage
# ============================================================

print("\nAggregated observation damage:")

aggregated_damage = {}

for node_id in sorted(set(result.touched_node_ids)):

    damage = obs_log.aggregate_damage(
        node_id,
        now=time.time(),
    )

    aggregated_damage[node_id] = damage

    print(
        f"  node={node_id} | "
        f"observed_damage={damage:.6f}"
    )


# ============================================================
# STEP 9: Apply observations -> G(t)
# ============================================================

G_t = apply_observations(
    G0,
    obs_log,
    now=time.time(),
)


# ============================================================
# STEP 10: Display affected G(t)
# ============================================================

print("\nAffected G(t) nodes:")

for node_id in sorted(set(result.touched_node_ids)):

    if node_id not in G_t.nodes:
        raise RuntimeError(
            f"Node returned by ingest does not exist in G(t): {node_id}"
        )

    node = G_t.nodes[node_id]

    print(
        f"  node={node_id} | "
        f"type={node['type']} | "
        f"damage={node['damage']:.6f} | "
        f"stress={node['stress']:.6f}"
    )


# ============================================================
# STEP 11: Assertions
# ============================================================

assert event["status"] == "analyzed"

assert event["model_name"] == "drone_detector_yolo26s"

assert event["detection_count"] == len(detections)

assert result.detections_seen == len(detections)

assert result.detections_ingested == len(detections)

assert result.detections_skipped_no_node == 0

assert len(result.touched_node_ids) > 0

for node_id in result.touched_node_ids:

    assert node_id in G_t.nodes

    assert len(obs_log._by_node[node_id]) > 0

    assert aggregated_damage[node_id] > 0.0

    assert G_t.nodes[node_id]["damage"] > 0.0


print("\n" + "=" * 70)
print("YOLO26s END-TO-END INGEST TEST PASSED")
print("=" * 70)
print("YOLO -> contract -> ingest -> ObservationLog -> damage -> G(t)")
print("=" * 70)

