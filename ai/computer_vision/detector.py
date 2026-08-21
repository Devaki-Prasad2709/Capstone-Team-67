"""Lazy-loading wrapper around the preserved YOLOv8 detector."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import numpy as np


class DamageDetector:
    def __init__(
        self,
        model_path: Path,
        confidence: float = 0.25,
        image_size: int = 640,
        device: str | None = None,
    ) -> None:
        if not model_path.is_file():
            raise FileNotFoundError(f"AI model not found: {model_path}")
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise RuntimeError(
                "AI dependencies are missing. Run: pip install -r requirements-ai.txt"
            ) from exc
        self.model_path = model_path
        self.confidence = confidence
        self.image_size = image_size
        self.device = device or None
        self.model = YOLO(str(model_path))

    def predict(self, images: Sequence[np.ndarray]) -> list[list[dict[str, Any]]]:
        if not images:
            return []
        results = self.model.predict(
            source=list(images),
            conf=self.confidence,
            imgsz=self.image_size,
            device=self.device,
            verbose=False,
        )
        output: list[list[dict[str, Any]]] = []
        for result in results:
            detections: list[dict[str, Any]] = []
            names = result.names
            if result.boxes is not None:
                for box in result.boxes:
                    class_id = int(box.cls.item())
                    detections.append(
                        {
                            "class_id": class_id,
                            "class_name": str(names.get(class_id, class_id)),
                            "confidence": round(float(box.conf.item()), 6),
                            "bbox": [round(float(value), 3) for value in box.xyxy[0].tolist()],
                        }
                    )
            output.append(detections)
        return output
