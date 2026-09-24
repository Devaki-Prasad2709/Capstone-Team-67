"""Verify preserved YOLO artifacts and serving-checkpoint metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_ROOT = ROOT / "ai" / "computer_vision"


def verify() -> dict[str, float]:
    manifest = json.loads((AI_ROOT / "artifact_manifest.json").read_text(encoding="utf-8"))
    for relative, expected in manifest["sha256"].items():
        path = AI_ROOT / relative
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual != expected:
            raise ValueError(f"Checksum mismatch for {relative}: {actual} != {expected}")

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise RuntimeError(
            "AI dependencies are required to verify metrics embedded in best.pt"
        ) from exc

    serving_checkpoint = AI_ROOT / "artifacts" / "drone_detector" / "weights" / "best.pt"
    checkpoint = YOLO(str(serving_checkpoint)).ckpt
    embedded = checkpoint.get("train_metrics") or {}
    observed = {
        "precision": float(embedded["metrics/precision(B)"]),
        "recall": float(embedded["metrics/recall(B)"]),
        "map50": float(embedded["metrics/mAP50(B)"]),
        "map50_95": float(embedded["metrics/mAP50-95(B)"]),
    }
    if observed != manifest["final_metrics"]:
        raise ValueError(f"Metric drift: {observed} != {manifest['final_metrics']}")
    return observed


def main() -> None:
    metrics = verify()
    print("AI artifacts: PASS")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
