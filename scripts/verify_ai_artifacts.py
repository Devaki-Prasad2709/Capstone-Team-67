"""Verify preserved YOLO artifacts and serving-checkpoint metrics."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AI_ROOT = ROOT / "ai" / "computer_vision"


def _metrics(embedded: dict[str, object], suffix: str) -> dict[str, float]:
    return {
        "precision": float(embedded[f"metrics/precision({suffix})"]),
        "recall": float(embedded[f"metrics/recall({suffix})"]),
        "map50": float(embedded[f"metrics/mAP50({suffix})"]),
        "map50_95": float(embedded[f"metrics/mAP50-95({suffix})"]),
    }


def verify() -> dict[str, dict[str, float]]:
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
    observed = {"box": _metrics(embedded, "B"), "mask": _metrics(embedded, "M")}
    expected = {"box": manifest["final_metrics"], "mask": manifest["mask_metrics"]}
    if observed != expected:
        raise ValueError(f"Metric drift: {observed} != {expected}")
    return observed


def main() -> None:
    metrics = verify()
    print("AI artifacts: PASS")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
