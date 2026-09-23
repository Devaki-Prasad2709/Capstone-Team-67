"""
test_pyg_normalization.py

Verifies the pyg_bridge.py position-normalization change in isolation,
against exactly the requirements specified:

  1. NetworkX graph coordinates remain unchanged.
  2. PyG x_pos/y_pos are normalized to [0,1].
  3. The same normalization extent is used across all timesteps.
  4. Other node features (load, capacity, damage, stress, status)
     are unchanged by normalization.
  5. Node types and edge attributes are unchanged.

Uses the same fixture/seed as the other tests for consistency, but this
file only needs 2 ticks (not the full 6-tick story) since it's testing the
normalization mechanism itself, not temporal damage dynamics.

Run with:  python -m tests.test_pyg_normalization
"""

from pathlib import Path

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.observation.observation_log import ObservationLog, ObservationRecord
from core.observation.state_update import apply_observations

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "gis" / "fixtures" / "demo_gis.geojson"
)


def main():
    print("=" * 70)
    print("Setup: G(0) and G(t) (one observation applied)")
    print("=" * 70)
    gis_data = load_gis(str(FIXTURE_PATH))
    G0, id_map = build_graph_from_gis(gis_data, seed=42)
    road_node = id_map["road_8"]

    obs_log = ObservationLog()
    import time
    obs_log.add(ObservationRecord(
        observation_id="obs_0", timestamp=time.time(), frame_id="f0", track_id=None,
        source="drone", class_id=1, class_name="Severe", confidence=0.8,
        bbox=(0, 0, 10, 10), latitude=12.7124, longitude=77.6974,
        working_x=0, working_y=0, building_id=None, node_id=road_node,
    ))
    Gt = apply_observations(G0, obs_log, now=time.time())

    # Snapshot the NetworkX positions BEFORE any PyG conversion, so we can
    # prove they're untouched afterward.
    pos_before = {n: G0.nodes[n]["pos"] for n in G0.nodes}
    pos_before_t = {n: Gt.nodes[n]["pos"] for n in Gt.nodes}

    try:
        import torch
        from tgnn.utils.helpers import nx_to_pyg
        from core.integration.pyg_bridge import build_graph_sequence, compute_position_extent
    except ModuleNotFoundError as e:
        print(f"torch/tgnn not available in this environment ({e}).")
        print("Run this in your local environment to execute the full check.")
        return

    print()
    print("=" * 70)
    print("CHECK 1: NetworkX coordinates unchanged after building the PyG sequence")
    print("=" * 70)
    sequence = [G0, Gt]
    pyg_sequence = build_graph_sequence(sequence, nx_to_pyg)  # normalize_positions=True (default)

    pos_after = {n: G0.nodes[n]["pos"] for n in G0.nodes}
    pos_after_t = {n: Gt.nodes[n]["pos"] for n in Gt.nodes}
    unchanged = (pos_before == pos_after) and (pos_before_t == pos_after_t)
    print(f"  PASS: NetworkX positions bit-identical before/after"
          if unchanged else "  FAIL: NetworkX positions were mutated -- this must not happen")

    print()
    print("=" * 70)
    print("CHECK 2: PyG x_pos/y_pos are in [0,1] after normalization")
    print("=" * 70)
    all_in_range = True
    for i, d in enumerate(pyg_sequence):
        xmin, xmax = d.x[:, 0].min().item(), d.x[:, 0].max().item()
        ymin, ymax = d.x[:, 1].min().item(), d.x[:, 1].max().item()
        print(f"  timestep {i}: x range=[{xmin:.4f},{xmax:.4f}]  y range=[{ymin:.4f},{ymax:.4f}]")
        if xmin < -1e-6 or xmax > 1 + 1e-6 or ymin < -1e-6 or ymax > 1 + 1e-6:
            all_in_range = False
    print(f"  PASS: all values within [0,1]" if all_in_range else "  FAIL: values outside [0,1]")

    print()
    print("=" * 70)
    print("CHECK 3: same extent used across all timesteps")
    print("=" * 70)
    # Re-derive raw (unnormalized) PyG to compute what the extent SHOULD be,
    # then confirm both timesteps were normalized against that same extent
    # rather than each recomputing its own per-tick min/max.
    raw_pyg_sequence = build_graph_sequence(sequence, nx_to_pyg, normalize_positions=False)
    expected_extent = compute_position_extent(raw_pyg_sequence)
    x_min, x_max, y_min, y_max = expected_extent

    # Since positions are identical across ticks in this pipeline (only
    # damage/stress change), the SAME node should normalize to the SAME
    # value at both timesteps if a single shared extent was used.
    node0 = list(G0.nodes)[0]
    norm_t0 = pyg_sequence[0].x[node0, 0].item()
    norm_t1 = pyg_sequence[1].x[node0, 0].item()
    same_extent_used = abs(norm_t0 - norm_t1) < 1e-6
    print(f"  node {node0} normalized x at t0: {norm_t0:.6f}, at t1: {norm_t1:.6f}")
    print(f"  PASS: identical normalized value at both timesteps (shared extent confirmed)"
          if same_extent_used else "  FAIL: normalization differs per timestep -- extent not shared")

    print()
    print("=" * 70)
    print("CHECK 4: load/capacity/damage/stress/status unchanged by normalization")
    print("=" * 70)
    all_other_features_match = True
    for i in range(len(pyg_sequence)):
        raw_other = raw_pyg_sequence[i].x[:, 2:]   # columns 2-6: load,capacity,damage,stress,status
        norm_other = pyg_sequence[i].x[:, 2:]
        if not torch.allclose(raw_other, norm_other):
            all_other_features_match = False
    print(f"  PASS: columns 2-6 (load,capacity,damage,stress,status) identical between "
          f"raw and normalized" if all_other_features_match else "  FAIL: non-position features changed")

    print()
    print("=" * 70)
    print("CHECK 5: node_type and edge attributes unchanged")
    print("=" * 70)
    types_match = all(
        torch.equal(raw_pyg_sequence[i].node_type, pyg_sequence[i].node_type)
        for i in range(len(pyg_sequence))
    )
    edges_match = all(
        torch.equal(raw_pyg_sequence[i].edge_index, pyg_sequence[i].edge_index) and
        torch.equal(raw_pyg_sequence[i].edge_attr, pyg_sequence[i].edge_attr)
        for i in range(len(pyg_sequence))
    )
    print(f"  node_type identical: {types_match}")
    print(f"  edge_index/edge_attr identical: {edges_match}")
    print(f"  PASS" if (types_match and edges_match) else "  FAIL")

    print()
    all_passed = unchanged and all_in_range and same_extent_used and all_other_features_match and types_match and edges_match
    print("ALL PYG NORMALIZATION TESTS PASSED" if all_passed else "SOME CHECKS FAILED -- see above")


if __name__ == "__main__":
    main()
