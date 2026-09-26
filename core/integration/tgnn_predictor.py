"""Validated checkpoint-backed TGNN inference for ordered GIS snapshots.

The checkpoint predicts next-timestep ``status``, where training encoded
``1 = operational`` and ``0 = failed``. Consequently sigmoid(logit) is an
operational score. The displayed failure-risk score is ``sigmoid(-logit)``.
Neither score is a calibrated real-world probability.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import networkx as nx
import torch

from config.settings import PROJECT_ROOT
from core.integration.pyg_bridge import build_graph_sequence
from tgnn.models.tgnn import TGNN
from tgnn.utils.helpers import (
    MODEL_INPUT_FEATURE_ORDER,
    NODE_FEATURE_ORDER,
    TARGET_FEATURE,
    nx_to_pyg,
)


DEFAULT_CHECKPOINT = PROJECT_ROOT / "tgnn" / "models" / "tgnn.pth"
DEFAULT_MANIFEST = PROJECT_ROOT / "tgnn" / "models" / "checkpoint_manifest.json"
RISK_INTERPRETATION = "uncalibrated_relative_failure_risk_score"
CALIBRATION_WARNING = (
    "The TGNN was trained on synthetic graph simulations and has no held-out "
    "Louisiana calibration set, probability calibration artifact, or validated "
    "operational threshold. Use scores for relative ranking and temporal deltas only."
)


def _checkpoint_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _checkpoint_manifest(checkpoint_path: Path) -> dict | None:
    manifest_path = checkpoint_path.with_name("checkpoint_manifest.json")
    if not manifest_path.is_file():
        return None
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected = manifest.get("sha256")
    actual = _checkpoint_sha256(checkpoint_path)
    if expected != actual:
        raise RuntimeError(
            f"TGNN checkpoint checksum mismatch: expected {expected}, got {actual}"
        )
    if manifest.get("node_tensor_feature_order") != list(NODE_FEATURE_ORDER):
        raise RuntimeError("TGNN checkpoint manifest feature order does not match code")
    return manifest


def _validated_node_order(snapshots: list[nx.DiGraph]) -> list[object]:
    if not snapshots:
        raise ValueError("TGNN inference requires at least one graph snapshot")
    node_order = list(snapshots[0].nodes)
    if not node_order:
        raise ValueError("TGNN inference cannot run on an empty graph")
    edge_order = list(snapshots[0].edges)
    previous_timestamp = None
    saw_timestamp = False

    for index, graph in enumerate(snapshots):
        if list(graph.nodes) != node_order:
            raise ValueError(
                f"Snapshot {index} has a different node order; TGNN outputs "
                "could not be mapped safely back to GIS node IDs"
            )
        if list(graph.edges) != edge_order:
            raise ValueError(f"Snapshot {index} has different topology or edge order")
        timestamp = graph.graph.get("snapshot_timestamp")
        if timestamp is None:
            if saw_timestamp:
                raise ValueError("Snapshot timestamp is missing after temporal ordering began")
            continue
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, (int, float))
            or not math.isfinite(timestamp)
        ):
            raise ValueError(f"Snapshot {index} timestamp must be a finite epoch number")
        timestamp = float(timestamp)
        if previous_timestamp is not None and timestamp <= previous_timestamp:
            raise ValueError("Snapshot timestamps must be strictly increasing")
        previous_timestamp = timestamp
        saw_timestamp = True
    return node_order


def load_checkpoint_model(
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    device: str | torch.device = "cpu",
) -> TGNN:
    """Load the committed TGNN state dictionary strictly on the requested device."""
    checkpoint_path = Path(checkpoint_path)
    if not checkpoint_path.is_file():
        raise FileNotFoundError(f"TGNN checkpoint not found: {checkpoint_path}")
    _checkpoint_manifest(checkpoint_path)
    resolved_device = torch.device(device)
    model = TGNN(input_dim=len(NODE_FEATURE_ORDER)).to(resolved_device)
    state = torch.load(checkpoint_path, map_location=resolved_device, weights_only=True)
    model.load_state_dict(state, strict=True)
    model.eval()
    return model


def predict_node_risk_report(
    snapshots: list[nx.DiGraph],
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    device: str | torch.device = "cpu",
) -> dict:
    """Return every timestep's logits, scores, ranks and GIS/node mappings."""
    node_order = _validated_node_order(snapshots)
    resolved_device = torch.device(device)
    sequence = [
        data.to(resolved_device)
        for data in build_graph_sequence(snapshots, nx_to_pyg)
    ]
    checkpoint_path = Path(checkpoint_path)
    model = load_checkpoint_model(checkpoint_path, resolved_device)

    with torch.no_grad():
        outputs = model(sequence)
    if len(outputs) != len(snapshots):
        raise RuntimeError("TGNN did not return one output tensor per snapshot")

    timeline = []
    for index, (graph, raw_output) in enumerate(zip(snapshots, outputs)):
        logits = raw_output.squeeze(-1)
        if logits.ndim != 1 or logits.shape[0] != len(node_order):
            raise RuntimeError(
                f"TGNN returned shape {tuple(raw_output.shape)} for "
                f"{len(node_order)} graph nodes"
            )
        operational_scores = torch.sigmoid(logits)
        failure_scores = torch.sigmoid(-logits)
        if not (
            torch.isfinite(logits).all()
            and torch.isfinite(operational_scores).all()
            and torch.isfinite(failure_scores).all()
        ):
            raise RuntimeError(f"TGNN produced non-finite output at snapshot {index}")
        ranking = torch.argsort(failure_scores, descending=True).detach().cpu().tolist()
        rank_by_row = {row: rank + 1 for rank, row in enumerate(ranking)}
        logits_list = logits.detach().cpu().tolist()
        operational_list = operational_scores.detach().cpu().tolist()
        failure_list = failure_scores.detach().cpu().tolist()
        nodes = []
        for row, graph_node_id in enumerate(node_order):
            nodes.append(
                {
                    "tensor_row": row,
                    "graph_node_id": graph_node_id,
                    "gis_source_id": graph.nodes[graph_node_id].get("gis_source_id"),
                    "node_type": graph.nodes[graph_node_id].get("type"),
                    "raw_logit": float(logits_list[row]),
                    "operational_score": float(operational_list[row]),
                    "failure_risk_score": float(failure_list[row]),
                    "risk_rank": rank_by_row[row],
                }
            )
        timeline.append(
            {
                "snapshot_index": index,
                "snapshot_timestamp": graph.graph.get("snapshot_timestamp"),
                "nodes": nodes,
            }
        )

    manifest = _checkpoint_manifest(checkpoint_path)
    return {
        "checkpoint_path": str(checkpoint_path),
        "checkpoint_sha256": _checkpoint_sha256(checkpoint_path),
        "checkpoint_manifest": manifest,
        "feature_order": list(NODE_FEATURE_ORDER),
        "model_input_features": list(MODEL_INPUT_FEATURE_ORDER),
        "training_target": TARGET_FEATURE,
        "training_target_encoding": {"operational": 1, "failed": 0},
        "tensor_shape_per_snapshot": [len(node_order), len(NODE_FEATURE_ORDER)],
        "sequence_length": len(snapshots),
        "risk_interpretation": RISK_INTERPRETATION,
        "calibrated_probability": False,
        "calibration_warning": CALIBRATION_WARNING,
        "timeline": timeline,
    }


def predict_node_risk(
    snapshots: list[nx.DiGraph],
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    device: str | torch.device = "cpu",
) -> dict[object, float]:
    """Return final relative failure-risk scores keyed by integer graph node ID."""
    report = predict_node_risk_report(snapshots, checkpoint_path, device)
    return {
        node["graph_node_id"]: node["failure_risk_score"]
        for node in report["timeline"][-1]["nodes"]
    }


def predict_gis_node_risk(
    snapshots: list[nx.DiGraph],
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
    device: str | torch.device = "cpu",
) -> dict[str, float]:
    """Return final scores keyed by stable original GIS source IDs."""
    report = predict_node_risk_report(snapshots, checkpoint_path, device)
    mapped = {}
    for node in report["timeline"][-1]["nodes"]:
        source_id = node["gis_source_id"]
        if not isinstance(source_id, str) or not source_id:
            raise ValueError(f"Graph node {node['graph_node_id']} has no stable GIS source ID")
        if source_id in mapped:
            raise ValueError(f"Duplicate GIS source ID in graph: {source_id}")
        mapped[source_id] = node["failure_risk_score"]
    return mapped
