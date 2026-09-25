"""Validate a finalized disaster scenario without requiring live services."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


ALLOWED_ASSET_STATUS = {"ready", "pending"}
ALLOWED_ORIGINS = {
    "real-openstreetmap-snapshot",
    "real-spacenet8",
    "real-isbda",
    "external-spacenet8-or-xbd",
    "external-isbda",
    "simulation",
    "engineering-contract",
}


def _bbox_close(first: list[float], second: list[float], tolerance: float = 1e-9) -> bool:
    return len(first) == len(second) == 4 and all(
        math.isclose(float(left), float(right), abs_tol=tolerance)
        for left, right in zip(first, second)
    )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def validate_scenario(
    manifest_path: Path,
    strict: bool = False,
    spacenet_root: Path | None = None,
    isbda_root: Path | None = None,
) -> dict[str, Any]:
    """Return a validation summary, raising ValueError for contract violations.

    Pending external imagery is valid in normal mode and reported in the result.
    Strict mode treats it as an error, making it suitable for the final demo gate.
    """
    manifest_path = manifest_path.resolve()
    root = manifest_path.parent
    manifest = _read_json(manifest_path)
    errors: list[str] = []
    pending: list[str] = []

    scenario_id = manifest.get("scenario_id")
    if not isinstance(scenario_id, str) or not scenario_id:
        errors.append("scenario_id must be a non-empty string")
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version must be 1.0")

    location = manifest.get("location") or {}
    bbox = location.get("bbox")
    center = location.get("center")
    if not (isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(x, (int, float)) and math.isfinite(x) for x in bbox)):
        errors.append("location.bbox must contain four finite WGS84 numbers")
    elif not (-180 <= bbox[0] < bbox[2] <= 180 and -90 <= bbox[1] < bbox[3] <= 90):
        errors.append("location.bbox must be ordered west,south,east,north")
    if not (isinstance(center, list) and len(center) == 2):
        errors.append("location.center must contain longitude and latitude")
    elif isinstance(bbox, list) and len(bbox) == 4 and not (bbox[0] <= center[0] <= bbox[2] and bbox[1] <= center[1] <= bbox[3]):
        errors.append("location.center must fall inside location.bbox")

    assets = manifest.get("assets") or []
    asset_ids: set[str] = set()
    for asset in assets:
        asset_id = asset.get("id")
        if not asset_id or asset_id in asset_ids:
            errors.append(f"asset id is missing or duplicated: {asset_id!r}")
            continue
        asset_ids.add(asset_id)
        status = asset.get("status")
        if status not in ALLOWED_ASSET_STATUS:
            errors.append(f"asset {asset_id}: unsupported status {status!r}")
        if asset.get("input_origin") not in ALLOWED_ORIGINS:
            errors.append(f"asset {asset_id}: unsupported input_origin")
        for field in ("original_dataset", "original_path", "sha256", "geographic_footprint"):
            if not asset.get(field):
                errors.append(f"asset {asset_id}: {field} is required for the frozen package")
        if not (asset.get("scenario_timestamp") or asset.get("scenario_timestamp_range")):
            errors.append(f"asset {asset_id}: scenario timestamp or range is required")
        if not isinstance(asset.get("simulation_fields"), list):
            errors.append(f"asset {asset_id}: simulation_fields must be a list")
        path = root / str(asset.get("path", ""))
        if status == "ready" and not path.exists():
            errors.append(f"asset {asset_id}: ready path does not exist: {path}")
        elif status == "ready" and path.is_file() and _sha256(path) != asset.get("sha256"):
            errors.append(f"asset {asset_id}: frozen SHA-256 does not match {path}")
        if status == "pending":
            pending.append(asset_id)

    offsets: list[float] = []
    for item in manifest.get("timeline") or []:
        offset = item.get("offset_seconds")
        if not isinstance(offset, (int, float)) or offset < 0:
            errors.append("timeline offsets must be non-negative numbers")
        else:
            offsets.append(float(offset))
        if item.get("source") not in asset_ids:
            errors.append(f"timeline event {item.get('event')!r} references an unknown source")
    if offsets != sorted(set(offsets)):
        errors.append("timeline offsets must be unique and strictly increasing")

    gis_path = root / "gis" / "infrastructure.geojson"
    gis_ids: set[str] = set()
    if gis_path.exists():
        gis = _read_json(gis_path)
        for feature in gis.get("features", []):
            props = feature.get("properties") or {}
            source_id = props.get("infrastructure_id") or props.get("road_id") or props.get("building_id")
            if source_id:
                gis_ids.add(str(source_id))
        gis_bbox = (gis.get("metadata") or {}).get("bbox")
        if bbox and gis_bbox and not _bbox_close(bbox, gis_bbox):
            errors.append("GIS metadata bbox does not match scenario bbox")
    else:
        errors.append("scenario GIS snapshot is missing")

    verified_external_sources: list[str] = []
    satellite_pair_path = root / "satellite" / "pair.json"
    if satellite_pair_path.exists():
        pair = _read_json(satellite_pair_path)
        if pair.get("scenario_id") != scenario_id:
            errors.append("satellite/pair.json: scenario_id does not match manifest")
        pre_bbox = (pair.get("pre_event") or {}).get("bbox")
        post_bbox = (pair.get("post_event") or {}).get("bbox")
        if not bbox or not pre_bbox or not _bbox_close(bbox, pre_bbox):
            errors.append("SpaceNet pre-event footprint does not match scenario bbox")
        if not bbox or not post_bbox or not _bbox_close(bbox, post_bbox, tolerance=1e-5):
            errors.append("SpaceNet post-event footprint does not align with scenario bbox")
        reference = pair.get("reference") or {}
        if reference.get("feature_count") != 40 or reference.get("flooded_feature_count") != 22:
            errors.append("SpaceNet reference counts must remain 40 total / 22 flooded")
        if spacenet_root is not None:
            error_count = len(errors)
            spacenet_root = Path(spacenet_root).resolve()
            for section in ("pre_event", "post_event", "annotation", "reference"):
                record = pair.get(section) or {}
                source = spacenet_root / str(record.get("relative_path", ""))
                if not source.is_file():
                    errors.append(f"SpaceNet {section} file does not exist: {source}")
                    continue
                digest = _sha256(source)
                if digest != record.get("sha256"):
                    errors.append(f"SpaceNet {section} checksum does not match pair.json")
                expected_size = record.get("size_bytes")
                if expected_size is not None and source.stat().st_size != expected_size:
                    errors.append(f"SpaceNet {section} size does not match pair.json")
            if len(errors) == error_count:
                verified_external_sources.append("spacenet8")

    drone_selection_path = root / "drone" / "selection.json"
    drone_asset_ids: set[str] = set()
    if drone_selection_path.exists():
        selection = _read_json(drone_selection_path)
        if selection.get("scenario_id") != scenario_id:
            errors.append("drone/selection.json: scenario_id does not match manifest")
        for record in selection.get("images", []):
            asset_id = record.get("asset_id")
            if not asset_id or asset_id in drone_asset_ids:
                errors.append(f"drone selection asset id is missing or duplicated: {asset_id!r}")
            drone_asset_ids.add(asset_id)
            if record.get("input_origin") != "real-isbda-image":
                errors.append(f"drone asset {asset_id}: image origin must be real-isbda-image")
            required_simulated = {"assigned_location", "target_id", "scenario_timestamp"}
            if not required_simulated.issubset(set(record.get("simulation_fields", []))):
                errors.append(f"drone asset {asset_id}: simulated context is not fully disclosed")
            location = record.get("assigned_location") or {}
            lon, lat = location.get("longitude"), location.get("latitude")
            if bbox and not (
                isinstance(lon, (int, float)) and isinstance(lat, (int, float))
                and bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]
            ):
                errors.append(f"drone asset {asset_id}: assigned location is outside scenario bbox")
            if record.get("target_id") not in gis_ids:
                errors.append(f"drone asset {asset_id}: target GIS id does not exist")
        if len(drone_asset_ids) != 3:
            errors.append("drone selection must contain exactly three frozen images")

        if isbda_root is not None:
            error_count = len(errors)
            isbda_root = Path(isbda_root).resolve()
            records = [selection.get("annotation") or {}] + list(selection.get("images", []))
            for record in records:
                source = isbda_root / str(record.get("relative_path", ""))
                label = record.get("asset_id", "annotation")
                if not source.is_file():
                    errors.append(f"ISBDA {label} file does not exist: {source}")
                    continue
                if _sha256(source) != record.get("sha256"):
                    errors.append(f"ISBDA {label} checksum does not match selection.json")
                if source.stat().st_size != record.get("size_bytes"):
                    errors.append(f"ISBDA {label} size does not match selection.json")
            if len(errors) == error_count:
                verified_external_sources.append("isbda")

    for binding, source_id in (manifest.get("bindings") or {}).items():
        if source_id not in gis_ids:
            errors.append(f"binding {binding}: GIS id {source_id!r} does not exist")

    event_ids: set[str] = set()
    social_event_ids: set[str] = set()
    scenario_start = _parse_timestamp((manifest.get("clock") or {}).get("scenario_start"))
    for filename in (
        "drone_events.json",
        "telemetry.json",
        "social_events.json",
        "responder_decisions.json",
    ):
        event_path = root / filename
        if not event_path.exists():
            continue
        document = _read_json(event_path)
        if document.get("scenario_id") != scenario_id:
            errors.append(f"{filename}: scenario_id does not match manifest")
        for event in document.get("events", []):
            event_id = event.get("event_id")
            if not event_id or event_id in event_ids:
                errors.append(f"event id is missing or duplicated: {event_id!r}")
            event_ids.add(event_id)
            if filename == "social_events.json":
                social_event_ids.add(event_id)
            timestamp = event.get("scenario_timestamp")
            offset = event.get("offset_seconds")
            if timestamp is None or offset is None or _parse_timestamp(timestamp) != scenario_start + timedelta(seconds=offset):
                errors.append(f"event {event_id}: scenario timestamp does not match its offset")
            if filename == "drone_events.json":
                if event.get("asset_id") not in drone_asset_ids:
                    errors.append(f"event {event_id}: unknown frozen drone asset")
                if event.get("input_origin") != "hybrid-real-image-simulated-context":
                    errors.append(f"event {event_id}: hybrid provenance is not disclosed")
                required = {"scenario_timestamp", "gps", "target_id"}
                if not required.issubset(set(event.get("simulation_fields", []))):
                    errors.append(f"event {event_id}: simulated drone fields are incomplete")
            if filename == "responder_decisions.json":
                if event.get("alert_id") not in social_event_ids:
                    errors.append(f"event {event_id}: responder decision references an unknown alert")
                if event.get("action") not in {"confirm", "report_false"}:
                    errors.append(f"event {event_id}: unsupported responder action")
            for target_id in ([event.get("target_id")] if event.get("target_id") else event.get("target_ids", [])):
                if target_id not in gis_ids:
                    errors.append(f"event {event_id}: GIS target {target_id!r} does not exist")

    timeline_path = root / "timeline.json"
    if timeline_path.exists():
        contract = _read_json(timeline_path)
        detailed_events = contract.get("events") or []
        expected_names = [
            "baseline_gis_graph_loaded",
            "satellite_post_event_change_detected",
            "first_drone_observation_arrives",
            "severe_damage_observations_arrive",
            "social_distress_report_arrives_pending",
            "responder_confirms_report",
            "road_telemetry_degrades",
            "graph_state_changes",
            "tgnn_recalculates_node_risks",
            "cascading_risk_pattern_becomes_visible",
            "recovery_information_arrives",
            "risk_is_recalculated_after_recovery",
        ]
        if contract.get("scenario_id") != scenario_id:
            errors.append("timeline.json: scenario_id does not match manifest")
        if contract.get("scenario_start") != (manifest.get("clock") or {}).get("scenario_start"):
            errors.append("timeline.json: scenario_start does not match manifest")
        if [event.get("sequence") for event in detailed_events] != list(range(1, 13)):
            errors.append("timeline.json must contain the ordered sequence 1 through 12")
        if [event.get("name") for event in detailed_events] != expected_names:
            errors.append("timeline.json event names do not match the locked disaster story")
        if [event.get("offset_seconds") for event in detailed_events] != offsets:
            errors.append("timeline.json offsets do not match the manifest summary")
        detailed_ids: set[str] = set()
        for event in detailed_events:
            event_id = event.get("event_id")
            if not event_id or event_id in detailed_ids:
                errors.append(f"timeline event id is missing or duplicated: {event_id!r}")
            detailed_ids.add(event_id)
            offset = event.get("offset_seconds")
            timestamp = event.get("scenario_timestamp")
            if timestamp is None or offset is None or _parse_timestamp(timestamp) != scenario_start + timedelta(seconds=offset):
                errors.append(f"timeline event {event_id}: timestamp does not match offset")
            input_contract = event.get("input") or {}
            target = event.get("target") or {}
            graph_effect = event.get("expected_graph_effect") or {}
            dashboard_effect = event.get("expected_dashboard_effect") or {}
            if not all(input_contract.get(field) for field in ("kind", "source", "reference")):
                errors.append(f"timeline event {event_id}: complete input contract is required")
            if not target.get("kind") or not target.get("ids"):
                errors.append(f"timeline event {event_id}: at least one explicit target is required")
            if not graph_effect.get("mutation") or not graph_effect.get("assertions"):
                errors.append(f"timeline event {event_id}: expected graph effect is required")
            if not dashboard_effect.get("view") or not dashboard_effect.get("assertions"):
                errors.append(f"timeline event {event_id}: expected dashboard effect is required")

    if strict and pending:
        errors.append("pending assets are not allowed in strict mode: " + ", ".join(pending))
    if errors:
        raise ValueError("Scenario validation failed:\n- " + "\n- ".join(errors))
    return {
        "scenario_id": scenario_id,
        "valid": True,
        "strict": strict,
        "asset_count": len(assets),
        "pending_assets": pending,
        "timeline_events": len(offsets),
        "gis_ids": len(gis_ids),
        "stream_events": len(event_ids),
        "verified_external_sources": verified_external_sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--strict", action="store_true", help="fail if any asset is pending")
    parser.add_argument(
        "--spacenet-root",
        type=Path,
        help="verify the registered external SpaceNet files and SHA-256 checksums",
    )
    parser.add_argument(
        "--isbda-root",
        type=Path,
        help="verify the registered external ISBDA files and SHA-256 checksums",
    )
    args = parser.parse_args()
    print(json.dumps(validate_scenario(
        args.manifest,
        strict=args.strict,
        spacenet_root=args.spacenet_root,
        isbda_root=args.isbda_root,
    ), indent=2))


if __name__ == "__main__":
    main()
