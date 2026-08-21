"""Legacy VisionPipeline directory demonstration retained as a portable CLI."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    from ai.computer_vision.vision_pipeline import VisionPipeline

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_directory", type=Path)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("ai/computer_vision/artifacts/drone_detector/weights/best.pt"),
    )
    args = parser.parse_args()
    pipeline = VisionPipeline(str(args.model))
    for image in sorted(args.image_directory.glob("*.jpg")):
        result = pipeline.process(str(image))
        if result["status"] == "completed":
            for item in result["results"]:
                print(item["metadata"]["image_path"], item["detections"])


if __name__ == "__main__":
    main()
