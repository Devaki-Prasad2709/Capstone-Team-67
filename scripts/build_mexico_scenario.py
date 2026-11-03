"""Build the small, version-controlled Mexico earthquake scenario package.

Raw xBD, CrisisMMD, and ISBDA assets remain on the external dataset drive.
Only contracts, checksums, selected labels, and a compact aligned GIS snapshot
are written into the repository.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from shapely.geometry import LineString, box, mapping
from shapely import wkt


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "scenarios" / "mexico_earthquake_2017"
SCENARIO_ID = "mexico-earthquake-2017-v1"
START = datetime(2026, 9, 19, 13, 14, tzinfo=timezone.utc)
BBOX = [-99.15778560304025, 19.28660372357156, -99.15318367079654, 19.29120565581528]
TILE = "mexico-earthquake_00000069"


def dump(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def timestamp(offset: int) -> str:
    return (START + timedelta(seconds=offset)).isoformat().replace("+00:00", "Z")


def external(path: Path, root: Path) -> dict:
    return {
        "relative_path": path.relative_to(root).as_posix(),
        "sha256": digest(path),
        "size_bytes": path.stat().st_size,
    }


def osm_roads() -> list[dict]:
    url = "https://api.openstreetmap.org/api/0.6/map?bbox=" + ",".join(map(str, BBOX))
    request = Request(url, headers={"User-Agent": "DisasterScope-Capstone/1.0"})
    root = ElementTree.fromstring(urlopen(request, timeout=60).read())
    nodes = {
        item.attrib["id"]: (float(item.attrib["lon"]), float(item.attrib["lat"]))
        for item in root.findall("node")
    }
    boundary = box(*BBOX)
    candidates = []
    for item in root.findall("way"):
        tags = {tag.attrib["k"]: tag.attrib["v"] for tag in item.findall("tag")}
        if "highway" not in tags:
            continue
        coords = [nodes[node.attrib["ref"]] for node in item.findall("nd") if node.attrib["ref"] in nodes]
        if len(coords) < 2:
            continue
        clipped = LineString(coords).intersection(boundary)
        if clipped.is_empty:
            continue
        if clipped.geom_type == "MultiLineString":
            clipped = max(clipped.geoms, key=lambda line: line.length)
        if clipped.geom_type != "LineString" or len(clipped.coords) < 2:
            continue
        candidates.append((clipped.length, item.attrib["id"], tags, clipped))
    candidates.sort(reverse=True)
    result = []
    for _, osm_id, tags, geometry in candidates[:5]:
        result.append({
            "type": "Feature",
            "properties": {
                "feature_class": "road_segment",
                "road_id": f"osm-way-{osm_id}",
                "name": tags.get("name", f"OSM {tags['highway']} {osm_id}"),
                "highway": tags["highway"],
                "lanes": tags.get("lanes"),
                "source": "OpenStreetMap API snapshot",
            },
            "geometry": mapping(geometry),
        })
    if len(result) < 3:
        raise RuntimeError("The OSM snapshot did not provide three usable roads")
    return result


def build() -> None:
    dataset_root = Path(os.getenv("XBD_DATASET_PATH", r"D:\Capstone-Team-67\datasets\Satellite\xBD"))
    crisis_root = Path(os.getenv("CRISISMMD_DATASET_PATH", r"D:\Capstone-Team-67\datasets\Social\CrisisMMD_v2.0"))
    isbda_root = Path(os.getenv("ISBDA_DATASET_PATH", r"D:\Capstone-Team-67\datasets\Drone\ISBDA"))
    split = dataset_root / "test"
    pre_image = split / "images" / f"{TILE}_pre_disaster.tif"
    post_image = split / "images" / f"{TILE}_post_disaster.tif"
    pre_label = split / "labels" / f"{TILE}_pre_disaster.json"
    post_label = split / "labels" / f"{TILE}_post_disaster.json"
    for required in (pre_image, post_image, pre_label, post_label):
        if not required.is_file():
            raise FileNotFoundError(required)

    label = json.loads(post_label.read_text(encoding="utf-8"))
    label_features = label["features"]["lng_lat"]
    damage_counts = Counter(item["properties"].get("subtype", "unknown") for item in label_features)
    damaged = [item for item in label_features if item["properties"].get("subtype") in {"major-damage", "destroyed"}]
    undamaged = [item for item in label_features if item["properties"].get("subtype") == "no-damage"][:8]
    buildings = []
    for item in damaged + undamaged:
        source_id = "xbd-building-" + item["properties"]["uid"]
        buildings.append({
            "type": "Feature",
            "properties": {
                "feature_class": "building",
                "building_id": source_id,
                "name": "xBD labelled building",
                "reference_damage": item["properties"].get("subtype"),
                "source": "xBD post-event label",
            },
            "geometry": mapping(wkt.loads(item["wkt"])),
        })
    roads = osm_roads()
    gis = {
        "type": "FeatureCollection",
        "metadata": {
            "source": "xBD building labels plus OpenStreetMap road snapshot",
            "bbox": BBOX,
            "crs": "EPSG:4326",
            "snapshot_retrieved_on": "2026-11-03",
            "raw_sources_committed": False,
        },
        "features": roads + buildings,
    }
    gis_path = OUTPUT / "gis" / "infrastructure.geojson"
    dump(gis_path, gis)
    primary_road, secondary_road = roads[0]["properties"]["road_id"], roads[1]["properties"]["road_id"]
    affected_building = buildings[0]["properties"]["building_id"]
    second_building = buildings[1]["properties"]["building_id"]
    building_centers = [wkt.loads(item["wkt"]).centroid for item in damaged[:2]]

    pair = {
        "schema_version": "1.0", "scenario_id": SCENARIO_ID,
        "dataset": "xBD Mexico Earthquake", "dataset_type": "xbd", "input_origin": "real-xbd",
        "tile_id": TILE, "dataset_root_env": "XBD_DATASET_PATH", "crs": "EPSG:4326",
        "pre_event": {**external(pre_image, dataset_root), "width": 1024, "height": 1024, "scenario_timestamp": timestamp(0), "bbox": BBOX},
        "post_event": {**external(post_image, dataset_root), "width": 1024, "height": 1024, "scenario_timestamp": timestamp(30), "bbox": BBOX},
        "annotation": {**external(post_label, dataset_root), "scenario_timestamp": timestamp(30)},
        "reference": {**external(post_label, dataset_root), "feature_count": len(label_features), "damage_counts": dict(damage_counts)},
        "provenance": {"imagery_and_labels": "real xBD", "scenario_time": "simulated", "raw_files_committed": False},
    }
    pair_path = OUTPUT / "satellite" / "pair.json"
    dump(pair_path, pair)

    louisiana_selection = json.loads((ROOT / "scenarios/louisiana_east_flood/drone/selection.json").read_text(encoding="utf-8"))
    selection = {**louisiana_selection, "scenario_id": SCENARIO_ID}
    targets = [primary_road, affected_building, secondary_road]
    points = [(BBOX[0] + 0.001, BBOX[1] + 0.003), (building_centers[0].x, building_centers[0].y), (BBOX[2] - 0.001, BBOX[1] + 0.002)]
    for index, image in enumerate(selection["images"]):
        image["assigned_location"] = {"longitude": points[index][0], "latitude": points[index][1], "crs": "EPSG:4326"}
        image["target_id"] = targets[index]
        image["scenario_timestamp"] = timestamp(60 if index == 0 else 90)
        image["geographic_footprint"] = "ISBDA source has no Mexico location; assignment is simulated"
    selection_path = OUTPUT / "drone" / "selection.json"
    dump(selection_path, selection)

    drone_events = []
    classes = ["Slight", "Severe", "Debris"]
    for index, image in enumerate(selection["images"]):
        offset = 60 if index == 0 else 90
        drone_events.append({
            "event_id": f"drone-{index + 1:03}", "offset_seconds": offset,
            "scenario_timestamp": timestamp(offset), "asset_id": image["asset_id"],
            "target_id": targets[index], "gps": {"longitude": points[index][0], "latitude": points[index][1]},
            "annotation_primary_class": classes[index], "input_origin": "hybrid-real-image-simulated-context",
            "real_fields": ["image_bytes", "official_annotations"],
            "simulation_fields": ["scenario_timestamp", "gps", "target_id"],
        })
    dump(OUTPUT / "drone_events.json", {"schema_version": "1.0", "scenario_id": SCENARIO_ID, "events": drone_events})

    crisis_tsv = crisis_root / "annotations" / "mexico_earthquake_final_data.tsv"
    with crisis_tsv.open(encoding="utf-8", newline="") as stream:
        records = list(csv.DictReader(stream, delimiter="\t"))
    source = next(item for item in records if item.get("text_human") == "infrastructure_and_utility_damage" and item.get("text_info") == "informative")
    source_contract = {
        "dataset": "CrisisMMD v2.0", "annotation_relative_path": crisis_tsv.relative_to(crisis_root).as_posix(),
        "annotation_sha256": digest(crisis_tsv), "tweet_id": source["tweet_id"], "image_id": source["image_id"],
        "tweet_text": source["tweet_text"], "text_human": source["text_human"], "image_human": source["image_human"],
        "input_origin": "real-crisismmd", "assigned_location_is_simulated": True,
    }
    social_source_path = OUTPUT / "social" / "source.json"
    dump(social_source_path, source_contract)
    social_point = building_centers[0]
    social = {
        "schema_version": "1.0", "scenario_id": SCENARIO_ID,
        "events": [{
            "event_id": "social-001", "offset_seconds": 120, "scenario_timestamp": timestamp(120),
            "text": source["tweet_text"], "label": "earthquake", "gps": {"longitude": social_point.x, "latitude": social_point.y},
            "target_ids": [affected_building, primary_road],
            "source_record_id": source["tweet_id"], "input_origin": "hybrid-real-crisismmd-simulated-context",
            "real_fields": ["text", "source_record_id"],
            "simulation_fields": ["scenario_timestamp", "gps", "target_ids"],
        }],
    }
    dump(OUTPUT / "social_events.json", social)
    telemetry = {
        "schema_version": "1.0", "scenario_id": SCENARIO_ID,
        "events": [
            {"event_id": "telemetry-001", "event_type": "road_degradation", "offset_seconds": 150, "scenario_timestamp": timestamp(150), "target_id": primary_road, "load": 0.76, "capacity": 0.8, "damage": 0.42, "input_origin": "simulation", "simulation_fields": ["scenario_timestamp", "target_id", "load", "capacity", "damage"]},
            {"event_id": "telemetry-002", "event_type": "road_recovery", "offset_seconds": 300, "scenario_timestamp": timestamp(300), "target_id": primary_road, "load": 0.46, "capacity": 0.8, "damage": 0.16, "input_origin": "simulation", "simulation_fields": ["scenario_timestamp", "target_id", "load", "capacity", "damage"]},
        ],
    }
    dump(OUTPUT / "telemetry.json", telemetry)
    dump(OUTPUT / "responder_decisions.json", {"schema_version": "1.0", "scenario_id": SCENARIO_ID, "events": [{"event_id": "decision-001", "offset_seconds": 135, "scenario_timestamp": timestamp(135), "alert_id": "social-001", "action": "confirm", "responder_id": "scenario-responder-mx-01", "input_origin": "simulation", "simulation_fields": ["scenario_timestamp", "action", "responder_id"]}]})

    event_specs = [
        (0, "baseline_gis_graph_loaded", "structural-gis", "graph", ["all"]),
        (30, "satellite_post_event_change_detected", "satellite-before-after", "broad_area", [TILE]),
        (60, "first_drone_observation_arrives", "drone-events", "gis_node", [primary_road]),
        (90, "severe_damage_observations_arrive", "drone-events", "gis_nodes", [affected_building, secondary_road]),
        (120, "social_distress_report_arrives_pending", "social-events", "alert_and_gis_nodes", ["social-001", affected_building]),
        (135, "responder_confirms_report", "responder-decisions", "alert", ["social-001"]),
        (150, "road_telemetry_degrades", "telemetry", "gis_node", [primary_road]),
        (180, "graph_state_changes", "timeline-contract", "graph_snapshot", ["G(180)"]),
        (210, "tgnn_recalculates_node_risks", "timeline-contract", "risk_set", ["all"]),
        (240, "cascading_risk_pattern_becomes_visible", "timeline-contract", "spatial_risk_cluster", ["top-risk-node-and-neighbors"]),
        (300, "recovery_information_arrives", "telemetry", "gis_node", [primary_road]),
        (330, "risk_is_recalculated_after_recovery", "timeline-contract", "risk_set", ["all"]),
    ]
    timeline_events = []
    for sequence, (offset, name, source_id, target_kind, target_ids) in enumerate(event_specs, 1):
        timeline_events.append({
            "sequence": sequence, "event_id": f"timeline-{sequence:02}-" + name.split("_")[0],
            "name": name, "offset_seconds": offset, "scenario_timestamp": timestamp(offset),
            "input": {"kind": "asset" if sequence <= 2 else "stream_or_derived_event", "source": source_id, "reference": source_id},
            "target": {"kind": target_kind, "ids": target_ids},
            "expected_graph_effect": {"mutation": "none" if sequence in {2, 5, 6, 9, 10, 12} else "scenario_state_transition", "assertions": ["preserve stable GIS IDs", "apply only evidence allowed by the existing architecture"]},
            "expected_dashboard_effect": {"view": "operations", "assertions": ["show this event at its scenario time", "retain real-versus-simulated provenance"]},
        })
    timeline_path = OUTPUT / "timeline.json"
    dump(timeline_path, {"schema_version": "1.0", "scenario_id": SCENARIO_ID, "scenario_start": timestamp(0), "speed": 1.0, "events": timeline_events})
    expected_path = OUTPUT / "expected_results.json"
    dump(expected_path, {"schema_version": "1.0", "scenario_id": SCENARIO_ID, "satellite": {"reference_damage_counts": dict(damage_counts)}, "isolation": {"no_cross_scenario_evidence": True}, "claims": {"real": ["xBD imagery and labels", "xBD building footprints", "OpenStreetMap road geometry", "CrisisMMD report text", "ISBDA image bytes"], "simulated": ["scenario timestamps", "drone GPS", "telemetry", "CrisisMMD GPS and GIS association", "responder decision"]}})

    assets = []
    def asset(asset_id, kind, relative_path, dataset, origin, times, simulated, footprint):
        path = OUTPUT / relative_path
        row = {"id": asset_id, "kind": kind, "status": "ready", "path": relative_path, "sha256": digest(path), "original_dataset": dataset, "original_path": relative_path, "input_origin": origin, "simulation_fields": simulated, "geographic_footprint": footprint}
        row.update(times)
        assets.append(row)
    asset("structural-gis", "gis", "gis/infrastructure.geojson", "xBD labels and OpenStreetMap", "real-xbd-osm-snapshot", {"scenario_timestamp": timestamp(0)}, ["scenario_timestamp"], "location.bbox")
    asset("satellite-before-after", "satellite", "satellite/pair.json", "xBD Mexico Earthquake", "real-xbd", {"scenario_timestamp_range": [timestamp(0), timestamp(30)]}, ["scenario_timestamp"], "location.bbox")
    asset("drone-images", "drone", "drone/selection.json", "ISBDA train split", "real-isbda", {"scenario_timestamp_range": [timestamp(60), timestamp(90)]}, ["assigned_location", "target_id", "scenario_timestamp"], "source location absent; Mexico locations simulated")
    asset("drone-events", "event-stream", "drone_events.json", "scenario event definition", "simulation", {"scenario_timestamp_range": [timestamp(60), timestamp(90)]}, ["scenario_timestamp", "gps", "target_id"], "assigned points inside location.bbox")
    asset("social-source", "social-source", "social/source.json", "CrisisMMD v2.0", "real-crisismmd", {"scenario_timestamp": timestamp(120)}, ["assigned_location", "target_ids", "scenario_timestamp"], "source report has no verified tile location")
    asset("social-events", "event-stream", "social_events.json", "CrisisMMD plus scenario assignment", "hybrid-real-crisismmd-simulated-context", {"scenario_timestamp": timestamp(120)}, ["scenario_timestamp", "gps", "target_ids"], "assigned point inside location.bbox")
    asset("responder-decisions", "event-stream", "responder_decisions.json", "scenario simulation", "simulation", {"scenario_timestamp": timestamp(135)}, ["scenario_timestamp", "action", "responder_id"], "linked to assigned report location")
    asset("telemetry", "event-stream", "telemetry.json", "scenario simulation", "simulation", {"scenario_timestamp_range": [timestamp(150), timestamp(300)]}, ["scenario_timestamp", "target_id", "load", "capacity", "damage"], "target GIS road inside location.bbox")
    asset("timeline-contract", "scenario-contract", "timeline.json", "engineering scenario contract", "engineering-contract", {"scenario_timestamp_range": [timestamp(0), timestamp(330)]}, [], "location.bbox")
    asset("acceptance-criteria", "validation", "expected_results.json", "engineering acceptance contract", "engineering-contract", {"scenario_timestamp": "not_applicable"}, [], "not applicable")
    manifest = {
        "schema_version": "1.0", "scenario_id": SCENARIO_ID, "title": "Mexico City Earthquake Response",
        "description": "A reproducible earthquake-response demonstration aligned to xBD Mexico tile 00000069.",
        "location": {"name": "Mexico City, Mexico", "crs": "EPSG:4326", "bbox": BBOX, "center": [(BBOX[0] + BBOX[2]) / 2, (BBOX[1] + BBOX[3]) / 2]},
        "clock": {"mode": "simulated", "scenario_start": timestamp(0), "speed": 1.0}, "assets": assets,
        "bindings": {"primary_road": primary_road, "secondary_road": secondary_road, "affected_building": affected_building, "secondary_building": second_building},
        "timeline": [{"offset_seconds": offset, "event": name, "source": source_id} for offset, name, source_id, _, _ in event_specs],
    }
    dump(OUTPUT / "scenario.json", manifest)
    print(f"Built {OUTPUT} with {len(buildings)} xBD buildings and {len(roads)} OSM roads")


if __name__ == "__main__":
    build()
