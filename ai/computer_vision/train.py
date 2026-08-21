"""Train or resume the drone damage detector using portable CLI paths."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True, help="Ultralytics dataset YAML")
    parser.add_argument("--model", default="yolov8n.pt")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch", type=int, default=8)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--device", default="")
    parser.add_argument("--name", default="drone_detector")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    from ultralytics import YOLO

    project = Path(__file__).resolve().parent / "runs"
    YOLO(args.model).train(
        data=str(args.data.resolve()), epochs=args.epochs, batch=args.batch,
        imgsz=args.imgsz, workers=args.workers, device=args.device,
        project=str(project), name=args.name, exist_ok=True, resume=args.resume,
        deterministic=True, seed=0,
    )


if __name__ == "__main__":
    main()
