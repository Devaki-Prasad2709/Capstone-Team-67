"""Legacy batched-inference demonstration retained as a portable CLI."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    from ai.computer_vision.inference_engine import InferenceEngine
    from ai.preprocessing.preprocessing_pipeline import PreprocessingPipeline

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument(
        "--model",
        type=Path,
        default=Path("ai/computer_vision/artifacts/drone_detector/weights/best.pt"),
    )
    args = parser.parse_args()
    result = PreprocessingPipeline().process(str(args.image))
    if result["status"] == "batch_ready":
        print(InferenceEngine(str(args.model)).infer(result["batch"]))
    else:
        print(result)


if __name__ == "__main__":
    main()
