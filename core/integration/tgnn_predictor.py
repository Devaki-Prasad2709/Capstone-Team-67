"""Checkpoint-backed TGNN inference for ordered GIS graph snapshots."""

from __future__ import annotations

from pathlib import Path

import networkx as nx
import torch

from config.settings import PROJECT_ROOT
from core.integration.pyg_bridge import build_graph_sequence
from tgnn.models.tgnn import TGNN
from tgnn.utils.helpers import nx_to_pyg


DEFAULT_CHECKPOINT = PROJECT_ROOT / "tgnn" / "models" / "tgnn.pth"


def _validated_node_order(snapshots: list[nx.DiGraph]) -> list[object]:
    if not snapshots:
        raise ValueError("TGNN inference requires at least one graph snapshot")

    node_order = list(snapshots[0].nodes)
    if not node_order:
        raise ValueError("TGNN inference cannot run on an empty graph")

    for index, graph in enumerate(snapshots[1:], start=1):
        if list(graph.nodes) != node_order:
            raise ValueError(
                f"Snapshot {index} has a different node order; TGNN outputs "
                "could not be mapped safely back to GIS node IDs"
            )
    return node_order


def load_checkpoint_model(
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    device: str | torch.device = "cpu",
) -> TGNN:
    """Load the committed TGNN state dictionary on the requested device."""
    checkpoint_path = Path(checkpoint_path)
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"TGNN checkpoint not found: {checkpoint_path}")

    resolved_device = torch.device(device)
    model = TGNN(input_dim=7).to(resolved_device)
    state = torch.load(checkpoint_path, map_location=resolved_device, weights_only=True)
    model.load_state_dict(state)
    model.eval()
    return model


def predict_node_risk(
    snapshots: list[nx.DiGraph],
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    device: str | torch.device = "cpu",
) -> dict[object, float]:
    """Return final-timestep risk keyed by the original GIS graph node ID.

    Position columns are normalized through the existing PyG bridge using one
    extent for the complete temporal sequence. All snapshots must preserve the
    same node insertion order so tensor rows map deterministically to node IDs.
    """
    node_order = _validated_node_order(snapshots)
    resolved_device = torch.device(device)
    sequence = [
        data.to(resolved_device)
        for data in build_graph_sequence(snapshots, nx_to_pyg)
    ]
    model = load_checkpoint_model(checkpoint_path, resolved_device)

    with torch.no_grad():
        logits = model(sequence)[-1].squeeze(-1)
        risks = torch.sigmoid(logits).detach().cpu().tolist()

    if len(risks) != len(node_order):
        raise RuntimeError(
            f"TGNN returned {len(risks)} rows for {len(node_order)} graph nodes"
        )
    return dict(zip(node_order, (float(value) for value in risks)))
