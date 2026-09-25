"""Pure pairing and result contract for broad-area satellite change evidence."""

from __future__ import annotations

import hashlib
import math
import time
from collections import Counter
from typing import Callable

from core.satellite.spacenet_adapter import classify_transferred_pair


REQUIRED_PAIR_FIELDS = (
    "image_id",
    "timestamp",
    "source",
    "transfer_mode",
    "satellite_phase",
    "tile_id",
    "bbox",
)


def validate_pair_event(event: dict) -> None:
    if not isinstance(event, dict):
        raise ValueError("Satellite pair event must be an object")
    missing = [field for field in REQUIRED_PAIR_FIELDS if field not in event]
    if missing:
        raise ValueError("Satellite pair event is missing: " + ", ".join(missing))
    if event["source"] != "satellite" or event["satellite_phase"] not in {"pre", "post"}:
        raise ValueError("Satellite pair requires source=satellite and phase pre/post")
    if not isinstance(event["timestamp"], (int, float)) or not math.isfinite(event["timestamp"]):
        raise ValueError("Satellite timestamp must be finite epoch seconds")
    bbox = event["bbox"]
    if not isinstance(bbox, list) or len(bbox) != 4:
        raise ValueError("Satellite pair event requires a four-number bbox")


def _source_reference(event: dict) -> dict:
    return {
        key: event.get(key)
        for key in (
            "image_id", "timestamp", "scenario_timestamp", "transfer_mode", "object_key",
            "image_uri", "content_hash", "size_bytes", "bbox", "satellite_phase",
        )
    }


def _summary(change: dict) -> dict:
    scored = [
        feature["properties"] for feature in change["features"]
        if feature["properties"]["diff_score"] is not None
    ]
    counts = Counter(item["severity"] for item in scored)
    scores = [item["diff_score"] for item in scored]
    return {
        "cell_count": len(change["features"]),
        "scored_cell_count": len(scored),
        "changed_cell_count": counts["moderate"] + counts["severe"],
        "severity_counts": {level: counts[level] for level in ("low", "moderate", "severe")},
        "mean_diff_score": round(sum(scores) / len(scores), 4) if scores else None,
        "max_diff_score": max(scores) if scores else None,
    }


def build_change_result(
    pre_event: dict,
    post_event: dict,
    image_loader: Callable[[dict], bytes],
    *,
    grid_size: int = 8,
) -> dict:
    """Build a dashboard-only change result from the transported image bytes."""
    validate_pair_event(pre_event)
    validate_pair_event(post_event)
    if pre_event["satellite_phase"] != "pre" or post_event["satellite_phase"] != "post":
        raise ValueError("build_change_result requires pre then post events")
    if pre_event["tile_id"] != post_event["tile_id"]:
        raise ValueError("Satellite pre/post tile IDs differ")
    if pre_event.get("scenario_id") != post_event.get("scenario_id"):
        raise ValueError("Satellite pre/post scenario IDs differ")
    change = classify_transferred_pair(
        image_loader(pre_event),
        image_loader(post_event),
        pre_event["bbox"],
        post_event["bbox"],
        grid_size=grid_size,
    )
    overlap = change["metadata"]["overlap_bounds"]
    digest = hashlib.sha256(
        f"{pre_event.get('content_hash')}:{post_event.get('content_hash')}".encode()
    ).hexdigest()[:16]
    return {
        "id": f"sat-change-{pre_event['tile_id']}-{digest}",
        "timestamp": float(post_event["timestamp"]),
        "processed_at": time.time(),
        "source": "satellite_change_analysis",
        "data_type": "broad_area_change",
        "scenario_id": post_event.get("scenario_id"),
        "scenario_timestamp": post_event.get("scenario_timestamp"),
        "tile_id": post_event["tile_id"],
        "source_images": {
            "pre": _source_reference(pre_event),
            "post": _source_reference(post_event),
        },
        "footprint": {
            "type": "Polygon",
            "coordinates": [[
                [overlap[0], overlap[1]], [overlap[2], overlap[1]],
                [overlap[2], overlap[3]], [overlap[0], overlap[3]],
                [overlap[0], overlap[1]],
            ]],
        },
        "change": change,
        "summary": _summary(change),
        "reference_labels": {
            "feature_count": post_event.get("reference_feature_count"),
            "flooded_feature_count": post_event.get("reference_flooded_feature_count"),
        },
        "interpretation": "broad-area uncalibrated radiometric change; not destruction probability",
        "tgnn_integration": "none",
    }


class SatellitePairAccumulator:
    """Pair pre/post records without changing either producer event."""

    def __init__(self, image_loader: Callable[[dict], bytes], *, grid_size: int = 8) -> None:
        self.image_loader = image_loader
        self.grid_size = grid_size
        self._pending: dict[tuple, dict[str, dict]] = {}
        self._emitted: set[tuple] = set()

    def accept(self, event: dict) -> dict | None:
        validate_pair_event(event)
        if event.get("is_duplicate"):
            return None
        key = (event.get("scenario_id"), event["tile_id"])
        pair = self._pending.setdefault(key, {})
        pair[event["satellite_phase"]] = event
        if not {"pre", "post"} <= pair.keys():
            return None
        fingerprint = (
            pair["pre"].get("content_hash"), pair["post"].get("content_hash")
        )
        emitted_key = (*key, *fingerprint)
        if emitted_key in self._emitted:
            return None
        result = build_change_result(
            pair["pre"], pair["post"], self.image_loader, grid_size=self.grid_size
        )
        self._emitted.add(emitted_key)
        return result
