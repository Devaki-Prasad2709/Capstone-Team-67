"""Convert COCO-style damage boxes into YOLO text labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def convert(annotations: Path, output: Path, category_offset: int = 1) -> int:
    coco = json.loads(annotations.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    images = {item["id"]: item for item in coco.get("images", [])}
    for image in images.values():
        (output / f"{Path(image['file_name']).stem}.txt").write_text("", encoding="utf-8")
    labels: dict[Path, list[str]] = {}
    count = 0
    for annotation in coco.get("annotations", []):
        image = images.get(annotation.get("image_id"))
        box = annotation.get("damage_bbox") or annotation.get("bbox")
        if not image or not box or len(box) != 4:
            continue
        x, y, width, height = map(float, box)
        image_width, image_height = float(image["width"]), float(image["height"])
        if width <= 0 or height <= 0 or image_width <= 0 or image_height <= 0:
            continue
        class_id = int(annotation["category_id"]) - category_offset
        line = (
            f"{class_id} {(x + width / 2) / image_width:.6f} "
            f"{(y + height / 2) / image_height:.6f} "
            f"{width / image_width:.6f} {height / image_height:.6f}\n"
        )
        path = output / f"{Path(image['file_name']).stem}.txt"
        labels.setdefault(path, []).append(line)
        count += 1
    for path, lines in labels.items():
        path.write_text("".join(lines), encoding="utf-8")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--category-offset", type=int, default=1)
    args = parser.parse_args()
    print(f"Converted {convert(args.annotations, args.output, args.category_offset)} annotations")


if __name__ == "__main__":
    main()
