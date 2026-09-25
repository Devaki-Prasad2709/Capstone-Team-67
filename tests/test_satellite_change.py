from __future__ import annotations

import io
from unittest.mock import patch

from PIL import Image

from core.satellite.change_contract import (
    SatellitePairAccumulator,
    build_change_result,
    validate_pair_event,
)
from dashboard.server import satellite_change_output


def _jpeg(value: int) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (16, 16), (value, value, value)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _event(phase: str, payload: bytes) -> dict:
    return {
        "image_id": f"{phase}.tif",
        "timestamp": 100.0 if phase == "pre" else 130.0,
        "scenario_timestamp": "2026-09-24T12:00:00Z" if phase == "pre" else "2026-09-24T12:00:30Z",
        "source": "satellite",
        "transfer_mode": "test",
        "satellite_phase": phase,
        "tile_id": "2_23_44",
        "bbox": [-90.1, 29.7, -90.0, 29.8],
        "scenario_id": "scenario",
        "content_hash": phase,
        "size_bytes": len(payload),
        "payload": payload,
        "reference_feature_count": 40,
        "reference_flooded_feature_count": 22,
    }


def test_transported_pair_produces_separate_broad_area_contract():
    pre, post = _event("pre", _jpeg(100)), _event("post", _jpeg(200))
    result = build_change_result(pre, post, lambda event: event["payload"], grid_size=4)

    assert result["data_type"] == "broad_area_change"
    assert result["tgnn_integration"] == "none"
    assert result["timestamp"] == post["timestamp"]
    assert result["reference_labels"] == {
        "feature_count": 40,
        "flooded_feature_count": 22,
    }
    assert len(result["change"]["features"]) == 16
    assert result["summary"]["changed_cell_count"] == 16
    assert result["footprint"]["type"] == "Polygon"
    assert "node_id" not in str(result)


def test_accumulator_pairs_out_of_order_and_emits_once():
    pre, post = _event("pre", _jpeg(100)), _event("post", _jpeg(110))
    accumulator = SatellitePairAccumulator(lambda event: event["payload"], grid_size=2)

    assert accumulator.accept(post) is None
    assert accumulator.accept(pre) is not None
    assert accumulator.accept(post) is None


def test_duplicate_and_invalid_events_do_not_become_change_results():
    pre = _event("pre", _jpeg(100))
    pre["is_duplicate"] = True
    accumulator = SatellitePairAccumulator(lambda event: event["payload"])
    assert accumulator.accept(pre) is None

    invalid = _event("pre", _jpeg(100))
    del invalid["bbox"]
    try:
        validate_pair_event(invalid)
    except ValueError as exc:
        assert "bbox" in str(exc)
    else:
        raise AssertionError("invalid pair event was accepted")


def test_dashboard_contract_adds_safe_minio_previews():
    latest = {
        "source_images": {
            "pre": {"object_key": "satellite/aa/pre.jpg"},
            "post": {"object_key": "satellite/bb/post.jpg"},
        }
    }
    with patch("dashboard.server.recent_topic_events", return_value=([latest], None)):
        response = satellite_change_output()
    assert response["error"] is None
    assert response["latest"]["source_images"]["pre"]["preview_url"] == (
        "/api/object?key=satellite/aa/pre.jpg"
    )
