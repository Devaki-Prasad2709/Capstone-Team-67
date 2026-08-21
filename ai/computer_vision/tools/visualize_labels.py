"""Draw YOLO labels onto an image and save the preview."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2


def visualize(image_path: Path, label_path: Path, output: Path) -> int:
    image = cv2.imread(str(image_path))
    if image is None:
        raise ValueError(f"Could not read {image_path}")
    height, width = image.shape[:2]
    count = 0
    for line in label_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        class_id, xc, yc, box_width, box_height = map(float, line.split())
        x1, y1 = int((xc - box_width / 2) * width), int((yc - box_height / 2) * height)
        x2, y2 = int((xc + box_width / 2) * width), int((yc + box_height / 2) * height)
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
        cv2.putText(image, f"Class {int(class_id)}", (x1, max(12, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        count += 1
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise OSError(f"Could not write {output}")
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--label", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(f"Drew {visualize(args.image, args.label, args.output)} boxes into {args.output}")


if __name__ == "__main__":
    main()
