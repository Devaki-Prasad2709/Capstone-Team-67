"""Pure helpers for consistent, testable AI result events."""

from __future__ import annotations

import time
from typing import Any


RESULT_TOPIC = "ai-analysis-results"
MODEL_NAME = "drone_detector_yolov8n"


def result_event(
    source_event: dict[str, Any],
    status: str,
    detections: list[dict[str, Any]] | None = None,
    preprocessing: dict[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    detections = detections or []
    return {
        "frame_id": str(source_event.get("frame_id", "unknown")),
        "source": str(source_event.get("source", "drone")),
        "source_topic": "drone-video",
        "source_timestamp": source_event.get("timestamp"),
        "object_key": source_event.get("object_key"),
        "content_hash": source_event.get("content_hash"),
        "canonical_image_id": source_event.get("canonical_image_id"),
        "status": status,
        "reason": reason,
        "model_name": MODEL_NAME,
        "detection_count": len(detections),
        "max_confidence": max((item["confidence"] for item in detections), default=0.0),
        "damage_classes": sorted({str(item["class_name"]) for item in detections}),
        "detections": detections,
        "preprocessing": preprocessing or {},
        "processed_at": time.time(),
    }
