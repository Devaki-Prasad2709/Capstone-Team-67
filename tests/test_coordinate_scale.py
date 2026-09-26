"""
test_coordinate_scale.py

STANDALONE DIAGNOSTIC. Does not modify tgnn/models/tgnn.py, the checkpoint,
core/observation/state_update.py, or core/graphs/gis_graph_builder.py.

Hypothesis under test: the synthetic training graph used x_pos/y_pos in
[0,1] (random.uniform(0,1) in the original graph_builder.py), but the real
GIS graph produces working-CRS coordinates in the hundreds of thousands
(UTM meters -- e.g. x=792,811-792,952, y=1,406,754-1,406,861 per the
diagnostic run). If the trained model's learned weights implicitly assume
position inputs are in a [0,1]-ish range, feeding it six-figure UTM
coordinates could dominate/saturate the input projection and explain the
near-identical outputs across structurally different nodes.

This script builds the EXACT same 6-tick sequence as tests/test_temporal.py
(same fixture, seed=42, same road/hospital observation stories), converts
it to PyG twice -- once with raw working-CRS coordinates (as production
currently does), once with x_pos/y_pos rescaled to [0,1] using the graph's
own spatial extent -- and runs the SAME trained checkpoint on both, so any
difference in behavior is attributable only to the coordinate scale.

Run with:  python -m tests.test_coordinate_scale
"""

import time
import copy
from pathlib import Path

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.observation.observation_log import ObservationLog, ObservationRecord
from core.observation.state_update import apply_observations
from core.integration.pyg_bridge import build_graph_sequence

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1] / "core" / "gis" / "fixtures" / "demo_gis.geojson"
)

TICK_INTERVAL_MINUTES = 10
NUM_TICKS = 6


def make_observation(obs_id, node_id, class_name, confidence, timestamp):
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
        latitude=12.7124,
        longitude=77.6974,
        working_x=0,
        working_y=0,
        building_id=None,
        node_id=node_id,
    )


def build_sequence():
    """Identical setup to tests/test_temporal.py -- same fixture, seed,
    ticks, and road/hospital observation stories, duplicated here rather
    than imported so this remains a fully standalone diagnostic file."""
    gis_data = load_gis(str(FIXTURE_PATH))
    G0, id_map = build_graph_from_gis(gis_data, seed=42)

    hospital_node = id_map["infra_2"]
    road_node = id_map["road_8"]

    obs_log = ObservationLog()
    t_start = time.time()
    tick_times = [t_start + i * TICK_INTERVAL_MINUTES * 60 for i in range(NUM_TICKS)]

    road_confidences = [0.55, 0.62, 0.71, 0.80, 0.85, 0.90]
    for i, (t, conf) in enumerate(zip(tick_times, road_confidences)):
        obs_log.add(make_observation(f"road_obs_{i}", road_node, "Severe", conf, t))

    obs_log.add(make_observation(
        "hosp_obs_0", hospital_node, "Slight", 0.65, tick_times[0]
    ))

    sequence = []
    prior_graph = G0
    for now in tick_times:
        Gt = apply_observations(prior_graph, obs_log, now=now)
        sequence.append(Gt)
        prior_graph = Gt

    return sequence, road_node, hospital_node


def normalize_positions(pyg_sequence):
    """
    Returns a NEW list of PyG Data objects (deep-copied, originals untouched)
    with x_pos (column 0) and y_pos (column 1) rescaled to [0,1] using the
    MIN/MAX ACROSS THE ENTIRE SEQUENCE (positions are static across ticks
    in this pipeline, but computed robustly rather than assumed). x and y
    are normalized independently using the graph's own spatial extent --
    not a fixed constant -- so this generalizes to any GIS area.
    """
    import torch

    all_x = torch.cat([d.x[:, 0] for d in pyg_sequence])
    all_y = torch.cat([d.x[:, 1] for d in pyg_sequence])
    x_min, x_max = all_x.min(), all_x.max()
    y_min, y_max = all_y.min(), all_y.max()

    print(f"  spatial extent used for normalization: "
          f"x=[{x_min:.2f}, {x_max:.2f}], y=[{y_min:.2f}, {y_max:.2f}]")

    normalized = []
    for d in pyg_sequence:
        d2 = copy.deepcopy(d)
        d2.x[:, 0] = (d2.x[:, 0] - x_min) / (x_max - x_min + 1e-9)
        d2.x[:, 1] = (d2.x[:, 1] - y_min) / (y_max - y_min + 1e-9)
        normalized.append(d2)
    return normalized


def run_and_report(label, pyg_sequence, model, road_node, hospital_node, torch):
    print()
    print(f"--- {label} ---")
    with torch.no_grad():
        outputs = model(pyg_sequence)

    road_risks, hosp_risks, diffs = [], [], []
    for i, out in enumerate(outputs):
        # status=1 means operational during training, so failure score is
        # the complementary sigmoid. This is a relative, uncalibrated score.
        sig = torch.sigmoid(-out).squeeze(-1)
        r = sig[road_node].item()
        h = sig[hospital_node].item()
        road_risks.append(r)
        hosp_risks.append(h)
        diffs.append(r - h)

    print(f"  road risk per tick:     {[round(v, 4) for v in road_risks]}")
    print(f"  hospital risk per tick: {[round(v, 4) for v in hosp_risks]}")
    print(f"  road-vs-hospital diff per tick: {[round(v, 5) for v in diffs]}")

    final_logits = outputs[-1].squeeze(-1)
    rounded = torch.round(final_logits * 1e6) / 1e6
    unique_vals = torch.unique(rounded)
    print(f"  distinct raw logits across all {final_logits.shape[0]} nodes "
          f"at final timestep: {unique_vals.numel()}")

    return road_risks, hosp_risks, diffs, unique_vals.numel()


def main():
    print("=" * 70)
    print("Setup: identical sequence to tests/test_temporal.py")
    print("=" * 70)
    sequence, road_node, hospital_node = build_sequence()
    print(f"road_node={road_node}, hospital_node={hospital_node}")

    try:
        import torch
        from tgnn.models.tgnn import TGNN
        from tgnn.utils.helpers import nx_to_pyg

        pyg_sequence_raw = build_graph_sequence(sequence, nx_to_pyg)

        print()
        print("=" * 70)
        print("Building normalized-coordinate version of the same sequence")
        print("=" * 70)
        pyg_sequence_norm = normalize_positions(pyg_sequence_raw)

        checkpoint_path = (
            Path(__file__).resolve().parents[1] / "tgnn" / "models" / "tgnn.pth"
        )
        state_dict = torch.load(checkpoint_path, map_location="cpu", weights_only=True)

        model = TGNN()
        model.load_state_dict(state_dict)
        model.eval()

        print()
        print("=" * 70)
        print("Running SAME trained checkpoint on both coordinate scales")
        print("=" * 70)

        raw_results = run_and_report(
            "CASE 1: raw working-CRS coordinates (production as-is)",
            pyg_sequence_raw, model, road_node, hospital_node, torch,
        )
        norm_results = run_and_report(
            "CASE 2: x_pos/y_pos normalized to [0,1]",
            pyg_sequence_norm, model, road_node, hospital_node, torch,
        )

        print()
        print("=" * 70)
        print("Summary")
        print("=" * 70)
        _, _, raw_diffs, raw_unique = raw_results
        _, _, norm_diffs, norm_unique = norm_results
        print(f"  distinct logits at final tick -- raw: {raw_unique}/14, normalized: {norm_unique}/14")
        print(f"  road-vs-hospital diff magnitude -- raw (final tick): {abs(raw_diffs[-1]):.5f}, "
              f"normalized (final tick): {abs(norm_diffs[-1]):.5f}")
        print()
        print("  If normalized diffs/distinct-logit-count are meaningfully larger than raw,")
        print("  that supports the coordinate-scale-mismatch hypothesis. If they are similarly")
        print("  small in both cases, coordinate scale is likely NOT the primary explanation")
        print("  and the near-identical outputs come from elsewhere (e.g. the checkpoint's")
        print("  learned weights generally, or another feature scale mismatch).")

    except ModuleNotFoundError as e:
        print(f"torch/tgnn modules not available in this environment ({e}).")
        print("This diagnostic must be run in the local environment with torch, ")
        print("torch-geometric, and tgnn/models/tgnn.pth available.")


if __name__ == "__main__":
    main()
