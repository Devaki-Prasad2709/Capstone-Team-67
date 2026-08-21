"""Run the preserved damage detector against one image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai.computer_vision.detector import DamageDetector
from ai.preprocessing import PreprocessingPipeline
from config.settings import settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--model", type=Path, default=settings.resolved_ai_model_path)
    parser.add_argument("--confidence", type=float, default=settings.ai_confidence_threshold)
    args = parser.parse_args()
    prepared = PreprocessingPipeline(
        image_size=settings.ai_image_size, enable_motion=False, enable_quality=False
    ).process_bytes(args.image.read_bytes())
    detector = DamageDetector(args.model, args.confidence, settings.ai_image_size, settings.ai_device)
    print(json.dumps(detector.predict([prepared.image])[0], indent=2))


if __name__ == "__main__":
    main()
