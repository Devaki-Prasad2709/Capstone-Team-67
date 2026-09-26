"""Verify clean-clone layout, dependency declarations, and model integrity."""

from __future__ import annotations

import argparse
import hashlib
import importlib
import json
from pathlib import Path
import subprocess
from typing import Any

from scripts.validate_scenario import validate_scenario
from scripts.verify_ai_artifacts import verify as verify_ai_artifacts


ROOT = Path(__file__).resolve().parents[1]
SCENARIO = ROOT / "scenarios" / "louisiana_east_flood" / "scenario.json"

FORBIDDEN_TRACKED_PREFIXES = (
    ".runtime/",
    ".venv/",
    "ai/computer_vision/dataset/",
    "ai/computer_vision/runs/",
    "datasets/",
    "logs/dashboard/",
    "runs/",
    "storage/checkpoints/",
    "storage/dedup/",
    "storage/processed/",
    "storage/received_images/",
)
FORBIDDEN_TRACKED_SUFFIXES = (".log", ".pyc", ".sqlite3", ".tmp")

REQUIRED_DISTRIBUTIONS = {
    "boto3",
    "fastapi",
    "kafka-python",
    "networkx",
    "numpy",
    "opencv-python",
    "pandas",
    "Pillow",
    "pydantic",
    "pyproj",
    "pyspark",
    "python-dotenv",
    "shapely",
    "torch",
    "torch-geometric",
    "torchvision",
    "ultralytics",
    "uvicorn",
}

IMPORT_SMOKE = (
    "boto3",
    "cv2",
    "fastapi",
    "kafka",
    "networkx",
    "numpy",
    "pandas",
    "PIL",
    "pydantic",
    "pyproj",
    "pyspark",
    "shapely",
    "torch",
    "torch_geometric",
    "torchvision",
    "ultralytics",
    "uvicorn",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tracked_files() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True, text=True
    )
    return [line.replace("\\", "/") for line in result.stdout.splitlines() if line]


def audit_layout() -> dict[str, Any]:
    tracked = _tracked_files()
    forbidden = [
        path
        for path in tracked
        if path != "logs/.gitkeep"
        and not (path.startswith("storage/") and path.endswith("/.gitkeep"))
        and (
            path.startswith(FORBIDDEN_TRACKED_PREFIXES)
            or path.lower().endswith(FORBIDDEN_TRACKED_SUFFIXES)
        )
    ]
    if forbidden:
        raise ValueError(f"Generated/raw files are tracked: {forbidden[:10]}")
    if ".env" in tracked:
        raise ValueError("Local .env must not be tracked")

    declared: set[str] = set()
    for filename in ("requirements.txt", "requirements-ai.txt"):
        for line in (ROOT / filename).read_text(encoding="utf-8").splitlines():
            item = line.strip()
            if item and not item.startswith(("#", "-r")):
                declared.add(item.split("==", 1)[0])
    missing = sorted(REQUIRED_DISTRIBUTIONS - declared)
    if missing:
        raise ValueError(f"Direct dependencies missing from requirements: {missing}")
    return {
        "tracked_files": len(tracked),
        "forbidden_tracked_files": 0,
        "declared_runtime_and_ai_dependencies": len(declared),
    }


def verify_tgnn_checkpoint() -> dict[str, Any]:
    model_dir = ROOT / "tgnn" / "models"
    manifest = json.loads(
        (model_dir / "checkpoint_manifest.json").read_text(encoding="utf-8")
    )
    checkpoint = model_dir / manifest["checkpoint"]
    actual_hash = _sha256(checkpoint)
    actual_size = checkpoint.stat().st_size
    if actual_hash != manifest["sha256"]:
        raise ValueError(f"TGNN checksum mismatch: {actual_hash} != {manifest['sha256']}")
    if actual_size != manifest["size_bytes"]:
        raise ValueError(f"TGNN size mismatch: {actual_size} != {manifest['size_bytes']}")
    return {"sha256": actual_hash, "size_bytes": actual_size}


def smoke_imports() -> list[str]:
    for module in IMPORT_SMOKE:
        importlib.import_module(module)
    return list(IMPORT_SMOKE)


def verify(include_imports: bool = True) -> dict[str, Any]:
    report: dict[str, Any] = {
        "layout": audit_layout(),
        "scenario": validate_scenario(SCENARIO),
        "yolo_metrics": verify_ai_artifacts(),
        "tgnn_checkpoint": verify_tgnn_checkpoint(),
    }
    if include_imports:
        report["import_smoke"] = smoke_imports()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-imports", action="store_true", help="skip third-party import smoke tests"
    )
    args = parser.parse_args()
    report = verify(include_imports=not args.skip_imports)
    print("Repository verification: PASS")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
