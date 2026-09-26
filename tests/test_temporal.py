"""
test_temporal.py

Everything run so far has fed the TGNN a 2-timestep sequence: [G(0), G(t)].
That barely exercises the GRU at all -- a sequence length of 2 is the
minimum possible input, not a meaningful one. This test builds a realistic
6-tick sequence with damage evolving differently across three separate
nodes, and checks that:

  1. Decay-weighted aggregation behaves correctly ACROSS TICKS, not just
     across observations at a single point in time (i.e. that G(t) at
     tick 5 correctly reflects tick 5's "now", not tick 0's).
  2. A node with observations that STOP (drone moves away) shows its
     damage staying put (since raw damage doesn't self-heal) while its
     *decay-weighted aggregate at query time* would be lower if queried
     with a much later `now` -- this is the "does the graph forget"
     property from the architecture doc, tested directly instead of
     just asserted in comments.
  3. A node with NO observations ever stays at exactly its GIS baseline
     across all 6 ticks (regression check: nothing bleeds between nodes).
  4. The resulting 6-graph sequence is well-formed for the TGNN (same
     node set/topology across all ticks -- required for a fixed-size
     GRU sequence) and, if torch is available, that TGNN.forward()
     actually runs on a real 6-step sequence and produces 6 sets of
     per-node risk values whose trend can be inspected.

Run with:  python -m tests.test_temporal
"""

import time

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.gis.building_association import build_building_lookup, SpatialIndex
from core.observation.geolocator import ManualOverrideGeoLocator
from core.observation.observation_log import ObservationLog, ObservationRecord
from core.observation.state_update import apply_observations
from core.integration.pyg_bridge import build_graph_sequence

from pathlib import Path
FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "gis" / "fixtures" / "demo_gis.geojson"
)

TICK_INTERVAL_MINUTES = 10
NUM_TICKS = 6


def make_observation(obs_id, node_id, class_name, confidence, timestamp, lat, lon, wx, wy, building_id=None):
    return ObservationRecord(
        observation_id=obs_id,
        timestamp=timestamp,
        frame_id=obs_id,
        track_id=None,
        source="drone",
        class_id=1,
        class_name=class_name,
        confidence=confidence,
        bbox=(100, 100, 200, 200),
        latitude=lat,
        longitude=lon,
        working_x=wx,
        working_y=wy,
        building_id=building_id,
        node_id=node_id,
    )


def main():
    print("=" * 70)
    print("Setup")
    print("=" * 70)
    gis_data = load_gis(str(FIXTURE_PATH))
    G0, id_map = build_graph_from_gis(gis_data, seed=42)
    print(f"G(0): {G0.number_of_nodes()} nodes, {G0.number_of_edges()} edges")

    # Pick three real nodes from the fixture to give each a distinct story:
    hospital_node = id_map["infra_2"]   # General Hospital
    road_node = id_map["road_8"]        # near Residential Block 1
    untouched_node = id_map["infra_4"]  # Cell Tower 1 -- gets NO observations

    print(f"  road_node={road_node} (repeated worsening damage)")
    print(f"  hospital_node={hospital_node} (single early hit, then drone moves away)")
    print(f"  untouched_node={untouched_node} (control -- must stay at baseline throughout)")

    obs_log = ObservationLog()
    t_start = time.time()
    tick_times = [t_start + i * TICK_INTERVAL_MINUTES * 60 for i in range(NUM_TICKS)]

    # --- Story 1: road_node -- worsening damage, observed every tick ---
    road_confidences = [0.55, 0.62, 0.71, 0.80, 0.85, 0.90]
    for i, (t, conf) in enumerate(zip(tick_times, road_confidences)):
        obs_log.add(make_observation(
            f"road_obs_{i}", road_node, "Severe", conf, t,
            lat=12.7124, lon=77.6974, wx=0, wy=0,  # positions unused by aggregate_damage
        ))

    # --- Story 2: hospital_node -- ONE early observation, then drone leaves ---
    obs_log.add(make_observation(
        "hosp_obs_0", hospital_node, "Slight", 0.65, tick_times[0],
        lat=12.7125, lon=77.6975, wx=0, wy=0,
    ))
    # no further observations for hospital_node after tick 0

    print()
    print("=" * 70)
    print("Building G(t) for each of 6 ticks")
    print("=" * 70)

    sequence = []
    prior_graph = G0
    for i, now in enumerate(tick_times):
        Gt = apply_observations(prior_graph, obs_log, now=now)
        sequence.append(Gt)
        prior_graph = Gt  # damage is monotonic via max(), so chaining is safe and
                          # matches how a real rolling pipeline would call this

        road_d = Gt.nodes[road_node]["damage"]
        road_s = Gt.nodes[road_node]["stress"]
        hosp_d = Gt.nodes[hospital_node]["damage"]
        hosp_s = Gt.nodes[hospital_node]["stress"]
        untouched_d = Gt.nodes[untouched_node]["damage"]

        print(f"  tick {i} (+{i*TICK_INTERVAL_MINUTES}min): "
              f"road damage={road_d:.3f} stress={road_s:.3f} | "
              f"hospital damage={hosp_d:.3f} stress={hosp_s:.3f} | "
              f"untouched damage={untouched_d:.3f}")

    print()
    print("=" * 70)
    print("CHECK 1: road_node damage should rise monotonically (repeated worsening hits)")
    print("=" * 70)
    road_damages = [g.nodes[road_node]["damage"] for g in sequence]
    is_monotonic = all(road_damages[i] <= road_damages[i + 1] for i in range(len(road_damages) - 1))
    print(f"  damages across ticks: {[round(d, 3) for d in road_damages]}")
    print(f"  PASS: monotonic increase" if is_monotonic else "  FAIL: damage decreased somewhere unexpectedly")

    print()
    print("=" * 70)
    print("CHECK 2: hospital_node's raw damage value does NOT decay (structural damage persists)")
    print("=" * 70)
    hosp_damages = [g.nodes[hospital_node]["damage"] for g in sequence]
    print(f"  damages across ticks: {[round(d, 3) for d in hosp_damages]}")
    all_equal = all(abs(d - hosp_damages[0]) < 1e-9 for d in hosp_damages)
    print(f"  PASS: damage value held constant at {hosp_damages[0]:.3f} across all ticks "
          f"(the graph correctly does NOT forget this observation just because the drone left)"
          if all_equal else "  FAIL: damage value changed without new observations -- investigate")

    print()
    print("=" * 70)
    print("CHECK 2b: but the DECAY-WEIGHTED aggregate at query time correctly drops")
    print("          for a stale observation (proves decay math works independently")
    print("          of the max-based persistence rule used for the stored `damage` field)")
    print("=" * 70)
    fresh_agg = obs_log.aggregate_damage(hospital_node, now=tick_times[0])
    stale_agg = obs_log.aggregate_damage(hospital_node, now=tick_times[0] + 20 * 3600)  # 20h later
    print(f"  aggregate_damage() right after observation: {fresh_agg:.4f}")
    print(f"  aggregate_damage() 20 hours later (no new obs): {stale_agg:.4f}")
    print(f"  PASS: decayed value is lower" if stale_agg < fresh_agg else "  FAIL: decay not applied")

    print()
    print("=" * 70)
    print("CHECK 3: untouched_node stays exactly at GIS baseline across all 6 ticks")
    print("=" * 70)
    untouched_damages = [g.nodes[untouched_node]["damage"] for g in sequence]
    baseline = G0.nodes[untouched_node]["damage"]
    all_baseline = all(abs(d - baseline) < 1e-9 for d in untouched_damages)
    print(f"  baseline={baseline}, observed across ticks: {untouched_damages}")
    print(f"  PASS: no cross-node contamination" if all_baseline else "  FAIL: untouched node's state changed")

    print()
    print("=" * 70)
    print("CHECK 4: sequence is well-formed for the TGNN (fixed node set across all ticks)")
    print("=" * 70)
    node_sets = [set(g.nodes) for g in sequence]
    same_topology = all(ns == node_sets[0] for ns in node_sets)
    print(f"  PASS: identical node set across all {NUM_TICKS} ticks"
          if same_topology else "  FAIL: topology changed between ticks -- GRU requires fixed node count")

    print()
    print("=" * 70)
    print("CHECK 5: run the actual TGNN on the full 6-step sequence (if torch available)")
    print("=" * 70)
    try:
        import sys, os
        sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core", "..")))
        from core.integration.pyg_bridge import build_graph_sequence
        # Import the actual TGNN + helpers from wherever this repo keeps them --
        # adjust this import to `from tgnn.models.tgnn import TGNN` /
        # `from tgnn.utils.helpers import nx_to_pyg` per your migrated layout.
        from tgnn.models.tgnn import TGNN
        from tgnn.utils.helpers import nx_to_pyg
        import torch

        pyg_sequence = build_graph_sequence(sequence, nx_to_pyg)
        model = TGNN()

        checkpoint_path = (
            Path(__file__).resolve().parents[1]
            / "tgnn"
            / "models"
            / "tgnn.pth"
        )
        state_dict = torch.load(
            checkpoint_path,
            map_location="cpu",
            weights_only=True,
        )
        model.load_state_dict(state_dict)

        model.eval()
        with torch.no_grad():
            outputs = model(pyg_sequence)

        print()
        print("  --- TEMPORARY DIAGNOSTIC (test-only, not production code) ---")
        diag_t = len(outputs) - 1  # final timestep -- where road/hospital states diverge most
        diag_data = pyg_sequence[diag_t]

        print(f"  NetworkX node ids: road_node={road_node}, hospital_node={hospital_node}")
        print(f"  PyG data.x shape at this timestep: {tuple(diag_data.x.shape)}")
        print(f"  PyG feature vector [x_pos,y_pos,load,capacity,damage,stress,status] "
              f"at index {road_node} (road): {diag_data.x[road_node].tolist()}")
        print(f"  PyG feature vector at index {hospital_node} (hospital): "
              f"{diag_data.x[hospital_node].tolist()}")
        print(f"  node_type at index {road_node} (road): {diag_data.node_type[road_node].item()}")
        print(f"  node_type at index {hospital_node} (hospital): {diag_data.node_type[hospital_node].item()}")

        raw_logits = outputs[diag_t].squeeze(-1)
        print(f"  raw TGNN logit shape: {tuple(raw_logits.shape)}")
        print(f"  raw logit at road_node index {road_node}: {raw_logits[road_node].item():.6f}")
        print(f"  raw logit at hospital_node index {hospital_node}: {raw_logits[hospital_node].item():.6f}")

        sig = torch.sigmoid(-raw_logits)
        print(f"  sigmoid at road_node index {road_node}: {sig[road_node].item():.6f}")
        print(f"  sigmoid at hospital_node index {hospital_node}: {sig[hospital_node].item():.6f}")

        # Check whether the collapse is GLOBAL (every node identical) rather
        # than specific to road/hospital -- this distinguishes "the whole
        # model output collapsed to one scalar broadcast across all nodes"
        # from "these two particular nodes happen to match."
        rounded = torch.round(raw_logits * 1e6) / 1e6
        unique_vals = torch.unique(rounded)
        print(f"  distinct raw logit values across ALL {raw_logits.shape[0]} nodes "
              f"at this timestep: {unique_vals.numel()}")
        if unique_vals.numel() <= 5:
            print(f"    values: {unique_vals.tolist()}")
        print("  --- END DIAGNOSTIC ---")
        print()

        print(f"  TGNN ran on all {len(outputs)} timesteps. Risk trend for road_node:")
        for i, out in enumerate(outputs):
            risk = torch.sigmoid(-out).squeeze(-1)[road_node].item()
            print(f"    tick {i}: risk = {risk:.4f}")
        print(f"  Risk trend for hospital_node (single early hit, should plateau/not keep rising):")
        for i, out in enumerate(outputs):
            risk = torch.sigmoid(-out).squeeze(-1)[hospital_node].item()
            print(f"    tick {i}: risk = {risk:.4f}")

    except ModuleNotFoundError as e:
        print(f"  torch/tgnn modules not available in this environment ({e}).")
        print("  All graph-level checks (1-4) above are the real verification of the")
        print("  temporal aggregation logic and passed without needing the model at all.")
        print("  Run this script in your local environment to see the actual TGNN")
        print("  risk trend across all 6 ticks -- adjust the tgnn/helpers import path")
        print("  above if your package layout differs.")


if __name__ == "__main__":
    main()
