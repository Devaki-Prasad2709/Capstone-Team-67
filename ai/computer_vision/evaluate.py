"""Evaluate a checkpoint against an Ultralytics dataset split."""

from __future__ import annotations

import argparse
from pathlib import Path

from config.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--model", type=Path, default=settings.resolved_ai_model_path)
    parser.add_argument("--split", choices=("train", "val", "test"), default="val")
    args = parser.parse_args()
    from ultralytics import YOLO

    metrics = YOLO(str(args.model)).val(data=str(args.data.resolve()), split=args.split)
    print(metrics.results_dict)


if __name__ == "__main__":
    main()
