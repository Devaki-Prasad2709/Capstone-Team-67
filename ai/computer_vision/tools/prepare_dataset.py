"""Create deterministic train/validation/test YOLO directory splits."""

from __future__ import annotations

import argparse
import random
import shutil
from pathlib import Path


def prepare(images: Path, labels: Path, output: Path, seed: int = 42) -> dict[str, int]:
    items = sorted(path for path in images.iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png"})
    random.Random(seed).shuffle(items)
    train_end, val_end = int(len(items) * 0.7), int(len(items) * 0.9)
    splits = {"train": items[:train_end], "val": items[train_end:val_end], "test": items[val_end:]}
    for split, paths in splits.items():
        image_output, label_output = output / "images" / split, output / "labels" / split
        image_output.mkdir(parents=True, exist_ok=True)
        label_output.mkdir(parents=True, exist_ok=True)
        for image in paths:
            shutil.copy2(image, image_output / image.name)
            label = labels / f"{image.stem}.txt"
            if label.is_file():
                shutil.copy2(label, label_output / label.name)
            else:
                (label_output / f"{image.stem}.txt").write_text("", encoding="utf-8")
    return {name: len(paths) for name, paths in splits.items()}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    print(prepare(args.images, args.labels, args.output, args.seed))


if __name__ == "__main__":
    main()
