"""Verify one segmentation prediction against its YOLO polygon labels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
from shapely.geometry import Polygon
from ultralytics import YOLO


def _polygon(points):
    if len(points) < 3:
        return Polygon()
    geometry = Polygon(points)
    return geometry if geometry.is_valid else geometry.buffer(0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("label", type=Path)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument(
        "--square-resize",
        action="store_true",
        help="match the worker preprocessing path before inference",
    )
    args = parser.parse_args()

    image = cv2.imread(str(args.image))
    if image is None:
        raise ValueError(f"Could not read image: {args.image}")
    height, width = image.shape[:2]

    truth: dict[int, list[Polygon]] = {}
    for line in args.label.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        class_id = int(fields[0])
        values = [float(value) for value in fields[1:]]
        points = [
            (values[index] * width, values[index + 1] * height)
            for index in range(0, len(values), 2)
        ]
        truth.setdefault(class_id, []).append(_polygon(points))

    inference_image = cv2.resize(image, (640, 640)) if args.square_resize else image
    inference_height, inference_width = inference_image.shape[:2]
    result = YOLO(str(args.model)).predict(
        source=inference_image, conf=args.confidence, imgsz=640, device="cpu", verbose=False
    )[0]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(args.output), result.plot())

    predictions = []
    for index, box in enumerate(result.boxes or []):
        class_id = int(box.cls.item())
        raw_points = result.masks.xy[index].tolist() if result.masks is not None else []
        points = [
            (x * width / inference_width, y * height / inference_height)
            for x, y in raw_points
        ]
        geometry = _polygon(points)
        same_class = truth.get(class_id, [])
        ious = [
            geometry.intersection(target).area / geometry.union(target).area
            for target in same_class
            if not geometry.is_empty and not target.is_empty and geometry.union(target).area
        ]
        in_bounds = all(
            0 <= float(x) <= width and 0 <= float(y) <= height for x, y in points
        )
        predictions.append({
            "class_id": class_id,
            "class_name": result.names[class_id],
            "confidence": round(float(box.conf.item()), 6),
            "mask_points": len(points),
            "mask_valid": not geometry.is_empty and geometry.is_valid,
            "mask_in_image_bounds": in_bounds,
            "mask_area_pixels": round(float(geometry.area), 1),
            "same_class_ground_truth_instances": len(same_class),
            "best_same_class_iou": round(max(ious, default=0.0), 4),
        })

    print(json.dumps({
        "image": str(args.image),
        "image_size": [width, height],
        "inference_image_size": [inference_width, inference_height],
        "ground_truth_instances": sum(len(items) for items in truth.values()),
        "prediction_count": len(predictions),
        "predictions": predictions,
        "preview": str(args.output),
    }, indent=2))


if __name__ == "__main__":
    main()
