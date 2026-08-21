"""Legacy preprocessing demonstration retained as a portable CLI example."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    from ai.preprocessing.preprocessing_pipeline import PreprocessingPipeline

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image_directory", type=Path)
    args = parser.parse_args()
    pipeline = PreprocessingPipeline()
    for image in sorted(args.image_directory.glob("*.jpg")):
        result = pipeline.process(str(image))
        if result["status"] == "batch_ready":
            print(f"Batch ready: {len(result['batch']['images'])} images")


if __name__ == "__main__":
    main()
