"""Pure helpers for consistent, testable AI result events."""

from __future__ import annotations

import time
from typing import Any


RESULT_TOPIC = "ai-analysis-results"
MODEL_NAME = "drone_detector_yolo26s"


def _gps_provenance(source_event: dict[str, Any]) -> str | None:
    simulated = set(source_event.get("simulation_fields") or [])
    return "simulated-scenario-assignment" if "gps" in simulated else None


def _detection_context(source_event: dict[str, Any]) -> dict[str, Any]:
    return {
        "image_reference": {
            "frame_id": source_event.get("frame_id"),
            "asset_id": source_event.get("asset_id"),
            "transfer_mode": source_event.get("transfer_mode"),
            "object_key": source_event.get("object_key"),
            "image_uri": source_event.get("image_uri"),
            "content_hash": source_event.get("content_hash"),
        },
        "scenario_timestamp": source_event.get("scenario_timestamp"),
        "gps": source_event.get("gps"),
        "gps_provenance": _gps_provenance(source_event),
        "target_association": {
            "declared_target_id": source_event.get("target_id"),
            "provenance": (
                "simulated-scenario-assignment"
                if "target_id" in set(source_event.get("simulation_fields") or [])
                else None
            ),
        },
    }


def result_event(
    source_event: dict[str, Any],
    status: str,
    detections: list[dict[str, Any]] | None = None,
    preprocessing: dict[str, Any] | None = None,
    reason: str | None = None,
    model_checkpoint_sha256: str | None = None,
) -> dict[str, Any]:
    context = _detection_context(source_event)
    detections = [{**item, **context} for item in (detections or [])]
    return {
        "frame_id": str(source_event.get("frame_id", "unknown")),
        "source": str(source_event.get("source", "drone")),
        "source_topic": "drone-video",
        "source_timestamp": source_event.get("timestamp"),
        "object_key": source_event.get("object_key"),
        "image_uri": source_event.get("image_uri"),
        "transfer_mode": source_event.get("transfer_mode"),
        "content_hash": source_event.get("content_hash"),
        "canonical_image_id": source_event.get("canonical_image_id"),
        "status": status,
        "reason": reason,
        "model_name": MODEL_NAME,
        "model_checkpoint_sha256": model_checkpoint_sha256,
        "inference_provenance": (
            "live-checkpoint-inference" if status == "analyzed" else "checkpoint-worker-no-inference"
        ),
        "prerecorded_output": False,
        "scenario_id": source_event.get("scenario_id"),
        "scenario_event_id": source_event.get("scenario_event_id"),
        "scenario_timestamp": source_event.get("scenario_timestamp"),
        "gps": source_event.get("gps"),
        "gps_provenance": context["gps_provenance"],
        "target_id": source_event.get("target_id"),
        "target_association_provenance": context["target_association"]["provenance"],
        "input_origin": source_event.get("input_origin"),
        "simulation_fields": source_event.get("simulation_fields") or [],
        "detection_count": len(detections),
        "max_confidence": max((item["confidence"] for item in detections), default=0.0),
        "damage_classes": sorted({str(item["class_name"]) for item in detections}),
        "detections": detections,
        "preprocessing": preprocessing or {},
        "processed_at": time.time(),
    }
