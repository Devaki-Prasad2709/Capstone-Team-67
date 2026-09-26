"""
test_stress_sensitivity_tgnn.py

STANDALONE. Does not import or modify core/observation/state_update.py's
DAMAGE_CAPACITY_EXPONENT_K, tgnn/models/tgnn.py, or the checkpoint.

IMPORTANT DISTINCTION FROM tests/test_stress_sensitivity.py:
That test only recomputed the `stress` formula directly and compared raw
stress numbers -- it never touched PyG or the TGNN, so it was NOT affected
by the coordinate-scale bug, and re-running it verbatim would reproduce the
exact same numbers as before (k=1.0 never saturates, k=1.5 saturates at
tick 3, k=2.0 saturates at tick 1 -- unchanged, because x_pos/y_pos never
entered that computation).

This test asks the next, now-answerable question: given each k's stress
trajectory, does the CORRECTED (position-normalized) pipeline produce a
meaningfully different TGNN risk trajectory? Before the coordinate fix this
question was moot -- the model was collapsing regardless of what stress
said. Now it isn't, so this comparison is actually informative.

For each k in {1.0, 1.5, 2.0}:
  1. Locally recompute damage/stress at each of the 6 ticks using that k
     (same formula as production state_update.py, duplicated here on
     purpose -- see test_stress_sensitivity.py for the same pattern).
  2. Build a graph sequence with those k-specific stress values (topology
     and every other feature identical to the real pipeline's G(t)).
  3. Run it through the corrected pyg_bridge (position normalization ON,
     as it now is by default) and the trained checkpoint.
  4. Report road and hospital risk trajectories for that k.

Run with:  python -m tests.test_stress_sensitivity_tgnn
"""

import time
import copy
from pathlib import Path

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.observation.observation_log import ObservationLog, ObservationRecord

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "gis" / "fixtures" / "demo_gis.geojson"
)

TICK_INTERVAL_MINUTES = 10
NUM_TICKS = 6
K_VALUES = [1.0, 1.5, 2.0]


def make_observation(obs_id, node_id, class_name, confidence, timestamp):
    return ObservationRecord(
        observation_id=obs_id, timestamp=timestamp, frame_id=obs_id, track_id=None,
        source="drone", class_id=1, class_name=class_name, confidence=confidence,
        bbox=(100, 100, 200, 200), latitude=12.7124, longitude=77.6974,
        working_x=0, working_y=0, building_id=None, node_id=node_id,
    )


def compute_stress_local(load: float, capacity: float, damage: float, k: float) -> float:
    """Literal copy of the production formula in state_update.py, parameterized
    by k. Duplicated deliberately, not imported -- keeps this a pure
    diagnostic with zero risk of touching the production constant."""
    effective_capacity = capacity * (1.0 - damage) ** k
    effective_capacity = max(effective_capacity, 1e-6)
    return min(1.0, load / effective_capacity)


def build_sequence_for_k(G0, id_map, obs_log, road_node, hospital_node, tick_times, k):
    """
    Builds a 6-graph sequence where road_node's and hospital_node's damage
    follow the SAME observation-derived damage trajectory as production
    (damage itself doesn't depend on k), but stress is recomputed locally
    using the given k. Every other node keeps its G0 baseline (unaffected,
    since only road_node/hospital_node received observations).
    """
    sequence = []
    road_prior_damage = G0.nodes[road_node]["damage"]
    hosp_prior_damage = G0.nodes[hospital_node]["damage"]

    for now in tick_times:
        Gt = copy.deepcopy(sequence[-1] if sequence else G0)

        road_damage_obs = obs_log.aggregate_damage(road_node, now=now)
        road_prior_damage = max(road_prior_damage, road_damage_obs)
        road_load = Gt.nodes[road_node]["load"]
        road_capacity = Gt.nodes[road_node]["capacity"]
        Gt.nodes[road_node]["damage"] = road_prior_damage
        Gt.nodes[road_node]["stress"] = compute_stress_local(road_load, road_capacity, road_prior_damage, k)

        hosp_damage_obs = obs_log.aggregate_damage(hospital_node, now=now)
        hosp_prior_damage = max(hosp_prior_damage, hosp_damage_obs)
        hosp_load = Gt.nodes[hospital_node]["load"]
        hosp_capacity = Gt.nodes[hospital_node]["capacity"]
        Gt.nodes[hospital_node]["damage"] = hosp_prior_damage
        Gt.nodes[hospital_node]["stress"] = compute_stress_local(hosp_load, hosp_capacity, hosp_prior_damage, k)

        sequence.append(Gt)

    return sequence


def main():
    print("=" * 70)
    print("Setup (identical fixture/seed/ticks/observations to prior tests)")
    print("=" * 70)
    gis_data = load_gis(str(FIXTURE_PATH))
    G0, id_map = build_graph_from_gis(gis_data, seed=42)
    road_node = id_map["road_8"]
    hospital_node = id_map["infra_2"]

    obs_log = ObservationLog()
    t_start = time.time()
    tick_times = [t_start + i * TICK_INTERVAL_MINUTES * 60 for i in range(NUM_TICKS)]

    road_confidences = [0.55, 0.62, 0.71, 0.80, 0.85, 0.90]
    for i, (t, conf) in enumerate(zip(tick_times, road_confidences)):
        obs_log.add(make_observation(f"road_obs_{i}", road_node, "Severe", conf, t))
    obs_log.add(make_observation("hosp_obs_0", hospital_node, "Slight", 0.65, tick_times[0]))

    try:
        import torch
        from tgnn.models.tgnn import TGNN
        from tgnn.utils.helpers import nx_to_pyg
        from core.integration.pyg_bridge import build_graph_sequence
    except ModuleNotFoundError as e:
        print(f"torch/tgnn not available in this environment ({e}).")
        print("Run this in your local environment to execute the full comparison.")
        return

    checkpoint_path = Path(__file__).resolve().parents[1] / "tgnn" / "models" / "tgnn.pth"
    state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
    model = TGNN()
    model.load_state_dict(state_dict)
    model.eval()

    print()
    print("=" * 70)
    print("Running each k's stress trajectory through the CORRECTED pipeline + trained TGNN")
    print("=" * 70)

    all_results = {}
    for k in K_VALUES:
        sequence = build_sequence_for_k(G0, id_map, obs_log, road_node, hospital_node, tick_times, k)
        pyg_sequence = build_graph_sequence(sequence, nx_to_pyg)  # normalize_positions=True (default)

        with torch.no_grad():
            outputs = model(pyg_sequence)

        road_risks = [torch.sigmoid(-o).squeeze(-1)[road_node].item() for o in outputs]
        hosp_risks = [torch.sigmoid(-o).squeeze(-1)[hospital_node].item() for o in outputs]
        road_stresses = [g.nodes[road_node]["stress"] for g in sequence]

        all_results[k] = (road_risks, hosp_risks, road_stresses)

        print(f"\nk = {k}")
        print(f"  road stress per tick:  {[round(s, 4) for s in road_stresses]}")
        print(f"  road risk per tick:    {[round(r, 4) for r in road_risks]}")
        print(f"  hospital risk per tick:{[round(r, 4) for r in hosp_risks]}")

    print()
    print("=" * 70)
    print("Summary: does k change the FINAL road risk meaningfully?")
    print("=" * 70)
    for k in K_VALUES:
        road_risks, hosp_risks, road_stresses = all_results[k]
        print(f"  k={k}: final road risk={road_risks[-1]:.4f}, "
              f"final road stress={road_stresses[-1]:.4f}, "
              f"road risk range across ticks=[{min(road_risks):.4f}, {max(road_risks):.4f}]")

    print()
    print("Compare this spread to the earlier stress-only sensitivity result")
    print("(k=1.0 never saturates, k=1.5 saturates tick 3, k=2.0 saturates tick 1)")
    print("to judge whether that stress-level difference actually reaches the")
    print("model's output in a way worth changing the production constant for.")


if __name__ == "__main__":
    main()
