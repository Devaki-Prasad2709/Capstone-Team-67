"""
pyg_bridge.py

Thin wrapper that builds the sequence of PyG Data objects the existing
TGNN.forward() expects. Uses the EXISTING, UNMODIFIED `nx_to_pyg` from
tgnn/utils/helpers.py -- this file adds nothing to the model's contract,
it only sequences snapshots and, as of this change, normalizes position
features to match the scale the checkpoint was trained on.

--------------------------------------------------------------------------
WHY POSITION NORMALIZATION LIVES HERE, NOT IN helpers.py OR state_update.py
--------------------------------------------------------------------------
Confirmed by direct experiment (test_coordinate_scale.py): the trained
checkpoint was fit on synthetic graphs with x_pos/y_pos in [0,1]. Real GIS
graphs produce working-CRS (UTM) coordinates in the hundreds of thousands.
Feeding those directly into the trained model collapsed its output almost
to a constant across structurally different nodes (12/14 distinct logits,
road-vs-hospital diff ~0.0000). Normalizing x_pos/y_pos to [0,1] restored
full node-level discrimination (14/14 distinct logits, diff ~0.56).

This is a PyG-tensor-level fix, not a graph-level or model-level one:
  - tgnn/utils/helpers.py (`nx_to_pyg`) stays byte-identical to the
    original, verified-hash file -- it has no opinion on real vs.
    synthetic coordinate scales and shouldn't need one.
  - core/observation/state_update.py and the GIS graph builder are
    untouched -- the real, unscaled working-CRS coordinates remain in
    NetworkX exactly as GIS/observations produced them. Nothing about the
    graph itself, spatial edge distances, or the dashboard overlay's
    reprojection changes.
  - The ONLY thing being reshaped is the two position columns of the
    tensor on its way INTO the model, at the one point in the codebase
    that already exists specifically as "the boundary between our graph
    and the existing model's expected input."

--------------------------------------------------------------------------
FIXED EXTENT ACROSS THE SEQUENCE (requirement: not independently per tick)
--------------------------------------------------------------------------
`build_graph_sequence()` computes ONE (x_min, x_max, y_min, y_max) from
every graph in the sequence combined, then applies that same extent to
every timestep. Since node positions don't change tick-to-tick in this
pipeline (only damage/stress/status do), this is equivalent to computing
it from any single tick today -- but computing it from the whole sequence
is the version that stays correct even if that assumption ever changes
(e.g. if the graph builder is later re-run mid-sequence with added nodes).
"""

import torch


def compute_position_extent(pyg_sequence, x_col: int = 0, y_col: int = 1):
    """
    One fixed extent, computed across every graph in the sequence combined.
    Returns (x_min, x_max, y_min, y_max) as plain floats (deterministic --
    no randomness, no dependency on iteration order beyond min/max, which
    are order-independent).
    """
    all_x = torch.cat([d.x[:, x_col] for d in pyg_sequence])
    all_y = torch.cat([d.x[:, y_col] for d in pyg_sequence])
    return (
        all_x.min().item(),
        all_x.max().item(),
        all_y.min().item(),
        all_y.max().item(),
    )


def normalize_position_columns(pyg_sequence, extent, x_col: int = 0, y_col: int = 1):
    """
    Returns a NEW list of PyG Data objects with ONLY columns x_col/y_col
    of `.x` rescaled to [0,1] using the given fixed extent. Every other
    field (load, capacity, damage, stress, status, node_type, edge_index,
    edge_attr) is left completely untouched -- copied through unchanged.

    Uses `.clone()` (PyG/torch's own deep-copy for tensors), so the input
    `pyg_sequence` and, transitively, the NetworkX graphs used to build it
    are never mutated.
    """
    x_min, x_max, y_min, y_max = extent
    x_range = x_max - x_min if (x_max - x_min) > 1e-12 else 1.0
    y_range = y_max - y_min if (y_max - y_min) > 1e-12 else 1.0

    normalized = []
    for d in pyg_sequence:
        d2 = d.clone()
        d2.x = d2.x.clone()
        d2.x[:, x_col] = (d2.x[:, x_col] - x_min) / x_range
        d2.x[:, y_col] = (d2.x[:, y_col] - y_min) / y_range
        normalized.append(d2)
    return normalized


def build_graph_sequence(snapshots: list, nx_to_pyg_fn, normalize_positions: bool = True) -> list:
    """
    snapshots: list of NetworkX graphs, one per timestep, oldest first
               (e.g. [G(t-2), G(t-1), G(t)]).
    nx_to_pyg_fn: the existing, unmodified tgnn/utils/helpers.nx_to_pyg
               function, passed in rather than imported directly so this
               module has no hard dependency on its exact location.
    normalize_positions: when True (the default -- this is now the correct
               behavior for the existing trained checkpoint, confirmed by
               experiment), rescales x_pos/y_pos to [0,1] using one fixed
               extent computed across the whole sequence. Set to False only
               for diagnostic comparisons against raw-coordinate behavior
               (e.g. reproducing the original test_coordinate_scale.py
               "Case 1" result) -- production callers should leave this at
               its default.

    Returns: list of PyG Data objects, ready to pass straight into
             TGNN.forward(graph_seq).
    """
    pyg_sequence = [nx_to_pyg_fn(G) for G in snapshots]

    if normalize_positions:
        extent = compute_position_extent(pyg_sequence)
        pyg_sequence = normalize_position_columns(pyg_sequence, extent)

    return pyg_sequence