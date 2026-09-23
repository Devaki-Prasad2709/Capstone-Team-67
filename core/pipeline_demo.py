"""
pipeline_demo.py

Proves the first milestone from the implementation plan end to end:

    GIS fixture -> Graph Builder -> G(0)
        -> synthetic/manual Observation Log
        -> decay-weighted damage -> effective capacity -> stress
        -> G(t)
        -> existing nx_to_pyg
        -> existing TGNN
        -> per-node risk

Run from the repository root with: python -m core.pipeline_demo

If torch / torch_geometric are not installed in this environment, the
script still runs through the GIS overlay (Step 5), then skips Step 6.
Step 6 is an untrained TGNN shape/forward-pass demo: no checkpoint is loaded,
and its output is not a trained risk prediction.
"""

import time
from pathlib import Path
from core.gis.gis_loader import load_gis, wgs84_to_working
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.gis.building_association import build_building_lookup, SpatialIndex
from core.observation.geolocator import Detection, ManualOverrideGeoLocator
from core.observation.observation_log import ObservationLog, ObservationRecord
from core.observation.state_update import apply_observations
from core.overlay.gis_overlay import build_node_overlay


def find_node_near(G, node_type, name_hint_pos, tolerance=50.0):
    """Small helper for this demo: find a node of a given type near a
    working-CRS position, so we can attach synthetic detections to a
    specific, human-recognizable node (e.g. 'General Hospital's nearest
    road') instead of a random one."""
    best, best_d = None, float("inf")
    for n, d in G.nodes(data=True):
        if d["type"] != node_type:
            continue
        dist = ((d["pos"][0] - name_hint_pos[0]) ** 2 + (d["pos"][1] - name_hint_pos[1]) ** 2) ** 0.5
        if dist < best_d:
            best, best_d = n, dist
    return best


def main():
    print("=" * 70)
    print("STEP 1: Load GIS (demo fixture) and build G(0)")
    print("=" * 70)
    fixture_path = Path(__file__).resolve().parent / "gis" / "fixtures" / "demo_gis.geojson"
    gis_data = load_gis(str(fixture_path))
    G0, id_map = build_graph_from_gis(gis_data, seed=42)
    print(f"G(0): {G0.number_of_nodes()} nodes, {G0.number_of_edges()} edges")

    spatial_edges = sum(1 for _, _, d in G0.edges(data=True) if d["edge_type"] == 0)
    dependency_edges = sum(1 for _, _, d in G0.edges(data=True) if d["edge_type"] == 1)
    print(f"  spatial edges: {spatial_edges}, dependency edges: {dependency_edges}")

    print()
    print("=" * 70)
    print("STEP 2: Building association lookup (one-time)")
    print("=" * 70)
    building_lookup = build_building_lookup(gis_data, id_map)
    spatial_index = SpatialIndex(gis_data, building_lookup, id_map)
    for bid, assoc in building_lookup.items():
        print(f"  {bid}: nearest_road={assoc.nearest_road}, "
              f"service_hospital={assoc.service_hospital}")

    print()
    print("=" * 70)
    print("STEP 3: Simulate drone detections (ManualOverrideGeoLocator -- test only)")
    print("=" * 70)
    # Pretend the drone flew over Residential Block 1 (B1) and observed
    # repeated severe damage over three passes -- exercising the decay/max
    # aggregation logic, exactly like the worked example in the design docs.
    geolocator = ManualOverrideGeoLocator()
    obs_log = ObservationLog()

    b1_lon, b1_lat = 77.6974, 12.7124  # inside Residential Block 1's footprint
    now = time.time()

    synthetic_passes = [
        {"class_name": "Severe", "confidence": 0.82, "minutes_ago": 40},
        {"class_name": "Severe", "confidence": 0.76, "minutes_ago": 25},
        {"class_name": "Severe", "confidence": 0.88, "minutes_ago": 5},
    ]

    for i, pass_info in enumerate(synthetic_passes):
        det = Detection(
            frame_id=f"frame_{i}",
            class_id=1,
            class_name=pass_info["class_name"],
            confidence=pass_info["confidence"],
            bbox=(100, 100, 200, 200),
            timestamp=str(now - pass_info["minutes_ago"] * 60),
        )
        geo = geolocator.locate(det, drone_telemetry={"override_lat": b1_lat, "override_lon": b1_lon})
        wx, wy = wgs84_to_working(geo.longitude, geo.latitude, gis_data.working_crs)
        resolved = spatial_index.resolve(wx, wy)

        record = ObservationRecord(
            observation_id=f"obs_{i}",
            timestamp=now - pass_info["minutes_ago"] * 60,
            frame_id=det.frame_id,
            track_id=None,
            source="drone",
            class_id=det.class_id,
            class_name=det.class_name,
            confidence=det.confidence,
            bbox=det.bbox,
            latitude=geo.latitude,
            longitude=geo.longitude,
            working_x=wx,
            working_y=wy,
            building_id=resolved["building_id"],
            node_id=resolved["node_id"],
        )
        obs_log.add(record)
        print(f"  pass {i}: {pass_info['class_name']} ({pass_info['confidence']}) "
              f"-> building={resolved['building_id']}, node_id={resolved['node_id']}")

    print()
    print("=" * 70)
    print("STEP 4: Apply observations -> G(t)")
    print("=" * 70)
    Gt = apply_observations(G0, obs_log, now=now)

    for node_id in obs_log.all_observed_node_ids():
        before = G0.nodes[node_id]
        after = Gt.nodes[node_id]
        print(f"  node {node_id} ({after['type']}): "
              f"damage {before['damage']:.3f} -> {after['damage']:.3f}, "
              f"stress {before['stress']:.3f} -> {after['stress']:.3f}")

    print()
    print("=" * 70)
    print("STEP 5: Build dashboard overlay from G(t)")
    print("=" * 70)
    overlay = build_node_overlay(Gt)
    print(f"  {len(overlay['features'])} features generated for /api/overlay/nodes")
    print(f"  sample feature: {overlay['features'][0]}")

    print()
    print("=" * 70)
    print("STEP 6: Build PyG sequence and run existing TGNN (if available)")
    print("=" * 70)
    try:
        import torch
        from core.integration.pyg_bridge import build_graph_sequence
        from tgnn.utils.helpers import nx_to_pyg
        from tgnn.models.tgnn import TGNN

        # A minimal 2-timestep sequence: G(0) then G(t). A real deployment
        # keeps a rolling window of the last N ticks; this demo just proves
        # the shapes line up end to end.
        sequence = build_graph_sequence([G0, Gt], nx_to_pyg)

        model = TGNN()
        model.eval()
        with torch.no_grad():
            outputs = model(sequence)

        last_step_risk = torch.sigmoid(outputs[-1]).squeeze(-1)
        print("  Untrained TGNN forward pass succeeded (no checkpoint loaded).")
        print("  Demo sigmoid outputs at G(t), not trained risk predictions:")
        for node_id in obs_log.all_observed_node_ids():
            print(f"    node {node_id}: demo output = {last_step_risk[node_id].item():.4f}")

    except ModuleNotFoundError as e:
        print(f"  torch/torch_geometric not available in this environment ({e}).")
        print("  Completed through STEP 5; skipped the untrained TGNN demo.")


if __name__ == "__main__":
    main()
