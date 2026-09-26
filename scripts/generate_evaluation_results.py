"""Generate a JSON evaluation snapshot from the live scenario topics and checkpoints."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import sys
import tempfile
import time
from urllib.request import urlopen

from kafka import KafkaConsumer, TopicPartition
import cv2
import networkx as nx
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.settings import settings
from consumers.observation_consumer import initialize_state, process_message
from core.integration.tgnn_predictor import predict_node_risk_report
from common.deduplication import ImageDeduplicator
from core.nlp.social_alert_workflow import SocialAlertWorkflow
from core.observation.state_update import apply_observations, recompute_node_state
from core.observation.telemetry import apply_telemetry_event
from dashboard.services import kafka_counts, minio_objects


SCENARIO = ROOT / "scenarios" / "louisiana_east_flood"
SCENARIO_ID = "louisiana-east-flood-v1"


def read_topic(topic: str) -> list[dict]:
    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers,
        group_id=None,
        enable_auto_commit=False,
        consumer_timeout_ms=1200,
    )
    try:
        partitions = consumer.partitions_for_topic(topic) or set()
        assigned = [TopicPartition(topic, item) for item in sorted(partitions)]
        if not assigned:
            return []
        consumer.assign(assigned)
        consumer.seek_to_beginning(*assigned)
        rows = []
        for message in consumer:
            try:
                event = json.loads(message.value.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if isinstance(event, dict):
                event["_kafka_partition"] = message.partition
                event["_kafka_offset"] = message.offset
                event["_kafka_timestamp_ms"] = message.timestamp
                rows.append(event)
        return rows
    finally:
        consumer.close()


def epoch(value: str) -> float:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def telemetry_event(item: dict) -> dict:
    return {
        "id": item["event_id"],
        "timestamp": epoch(item["scenario_timestamp"]),
        "target_id": item["target_id"],
        "load": item["load"],
        "capacity": item["capacity"],
        "damage": item["damage"],
        "event_type": item["event_type"],
    }


def top_five(snapshot: dict) -> list[dict]:
    ranked = sorted(snapshot["nodes"], key=lambda item: item["risk_rank"])
    return [
        {
            "rank": item["risk_rank"],
            "gis_source_id": item["gis_source_id"],
            "node_type": item["node_type"],
            "relative_failure_risk": round(item["failure_risk_score"], 6),
        }
        for item in ranked[:5]
    ]


def state_row(graph: nx.DiGraph, node_id: int) -> dict:
    node = graph.nodes[node_id]
    return {
        key: node.get(key)
        for key in (
            "load", "capacity", "damage", "effective_capacity", "utilization",
            "stress", "status_label", "freshness_status",
        )
    }


def spark_latency() -> dict:
    selection = json.loads((SCENARIO / "drone" / "selection.json").read_text(encoding="utf-8"))
    hashes = {item["sha256"] for item in selection["images"]}
    image_ids = {
        json.loads((SCENARIO / "satellite" / "pair.json").read_text(encoding="utf-8"))[phase]["relative_path"].split("/")[-1]
        for phase in ("pre_event", "post_event")
    }
    result = {}
    for layer in ("drone", "satellite", "ai", "social", "gis"):
        files = list((ROOT / "storage" / "processed" / layer).glob("*.parquet"))
        entry = {"parquet_files": len(files), "scenario_latency_ms": None}
        if not files:
            result[layer] = entry
            continue
        frame = pd.concat((pd.read_parquet(path) for path in files), ignore_index=True)
        if layer == "drone":
            frame = frame[frame.get("content_hash").isin(hashes)]
            processed_column = "processed_at"
        elif layer == "satellite":
            frame = frame[frame.get("image_id").isin(image_ids)]
            processed_column = "processed_at"
        elif layer == "ai":
            frame = frame[frame.get("scenario_id") == SCENARIO_ID]
            processed_column = "spark_processed_at"
        elif layer == "social":
            frame = frame[frame.get("event") == SCENARIO_ID]
            processed_column = "processed_at"
        else:
            result[layer] = entry
            continue
        if frame.empty or processed_column not in frame or "kafka_timestamp" not in frame:
            result[layer] = entry
            continue
        processed = pd.to_datetime(frame[processed_column], errors="coerce")
        received = pd.to_datetime(frame["kafka_timestamp"], errors="coerce")
        values = ((processed - received).dt.total_seconds() * 1000).dropna()
        values = values[values >= 0].tolist()
        if values:
            ordered = sorted(float(value) for value in values)
            fresh = [value for value in ordered if value <= 60_000]
            entry["scenario_latency_ms"] = {
                "all_replay_records": {
                "samples": len(ordered),
                "min": round(ordered[0], 1),
                "median": round(statistics.median(ordered), 1),
                "p95": round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)], 1),
                "max": round(ordered[-1], 1),
                },
                "records_processed_within_60_seconds": {
                    "samples": len(fresh),
                    "min": round(fresh[0], 1) if fresh else None,
                    "median": round(statistics.median(fresh), 1) if fresh else None,
                    "max": round(fresh[-1], 1) if fresh else None,
                },
            }
        result[layer] = entry
    return result


def api_latency(base_url: str | None) -> dict | None:
    if not base_url:
        return None
    result = {}
    for name, path in (
        ("status", "/api/status"),
        ("operations", "/api/operations"),
        ("social", "/api/social/alerts?scenario_only=true"),
        ("satellite", "/api/satellite/change"),
    ):
        started = time.perf_counter()
        with urlopen(base_url.rstrip("/") + path, timeout=30) as response:
            response.read()
        result[name] = round((time.perf_counter() - started) * 1000, 1)
    return result


def cascade_fixture() -> dict:
    graph = nx.DiGraph(working_crs="EPSG:32615", snapshot_sequence=0)
    for node_id, node_type in enumerate(("power", "telecom", "social")):
        graph.add_node(
            node_id, gis_source_id=f"node-{node_id}", type=node_type,
            pos=(float(node_id), 0.0), load=0.2, capacity=1.0, damage=0.0,
            dependency_factor=1.0, last_observation_timestamp=None,
            freshness_seconds=None, freshness_status="missing",
        )
        recompute_node_state(graph.nodes[node_id])
    graph.add_edge(0, 1, edge_type=1, weight=1.0, delay=1)
    graph.add_edge(1, 2, edge_type=1, weight=1.0, delay=1)
    failed = apply_telemetry_event(graph, {"power": 0}, {
        "id": "power-failure", "timestamp": 10.0, "target_id": "power",
        "load": 0.9, "capacity": 1.0, "damage": 0.8,
        "event_type": "power_degradation",
    })
    restored = apply_telemetry_event(failed, {"power": 0}, {
        "id": "power-recovery", "timestamp": 20.0, "target_id": "power",
        "load": 0.2, "capacity": 1.0, "damage": 0.0,
        "event_type": "power_recovery",
    })
    return {
        "scenario_dependency_edges": 0,
        "fixture_only": True,
        "failure": [
            {
                "type": failed.nodes[node]["type"],
                "damage": failed.nodes[node]["damage"],
                "dependency_factor": failed.nodes[node]["dependency_factor"],
                "stress": failed.nodes[node]["stress"],
                "status": failed.nodes[node]["status_label"],
            }
            for node in failed.nodes
        ],
        "recovery": [
            {
                "type": restored.nodes[node]["type"],
                "damage": restored.nodes[node]["damage"],
                "dependency_factor": restored.nodes[node]["dependency_factor"],
                "stress": restored.nodes[node]["stress"],
                "status": restored.nodes[node]["status_label"],
            }
            for node in restored.nodes
        ],
    }


def validate_phash(asset: dict) -> dict:
    path = Path(settings.isbda_dataset_path).expanduser() / asset["relative_path"]
    original_bytes = path.read_bytes()
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Could not decode {path}")
    encoded, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 82])
    if not encoded:
        raise ValueError(f"Could not recompress {path}")
    recompressed = buffer.tobytes()
    with tempfile.TemporaryDirectory(prefix="disaster-eval-phash-") as folder:
        dedup = ImageDeduplicator(Path(folder) / "fingerprints.sqlite3")
        original = dedup.check_and_register("drone", asset["asset_id"], original_bytes, image)
        near = dedup.check_and_register(
            "drone", asset["asset_id"] + "-recompressed", recompressed,
            cv2.imdecode(buffer, cv2.IMREAD_COLOR),
        )
    return {
        "source_asset": asset["asset_id"],
        "recompressed_sha256_differs": original.content_hash != near.content_hash,
        "is_duplicate": near.is_duplicate,
        "method": near.duplicate_method,
        "hamming_distance": near.perceptual_distance,
        "canonical_id": near.canonical_id,
    }


def render_yolo_evidence(folder: Path, asset: dict, result: dict) -> str:
    path = Path(settings.isbda_dataset_path).expanduser() / asset["relative_path"]
    image = cv2.imread(str(path))
    if image is None:
        raise ValueError(f"Could not decode {path}")
    canvas = cv2.resize(image, (640, 640), interpolation=cv2.INTER_AREA)
    colors = {"Slight": (70, 220, 120), "Severe": (55, 80, 255), "Debris": (0, 180, 255)}
    for detection in result.get("detections") or []:
        x1, y1, x2, y2 = (int(round(value)) for value in detection["bbox"])
        color = colors.get(detection["class_name"], (255, 255, 255))
        cv2.rectangle(canvas, (x1, y1), (x2, y2), color, 2)
        label = f'{detection["class_name"]} {detection["confidence"]:.2f}'
        cv2.putText(canvas, label, (x1, max(16, y1 - 5)), cv2.FONT_HERSHEY_SIMPLEX, 0.42, color, 1, cv2.LINE_AA)
    output = folder / f'yolo_{Path(asset["relative_path"]).stem}.png'
    cv2.imwrite(str(output), canvas)
    return output.name


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dashboard-url", default=None)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    topics = {
        name: read_topic(name)
        for name in (
            "drone-video", "satellite-imagery", "social-posts",
            "ai-analysis-results", "infrastructure-telemetry",
            "satellite-change-results",
        )
    }
    selection = json.loads((SCENARIO / "drone" / "selection.json").read_text(encoding="utf-8"))
    assets = {item["sha256"]: item for item in selection["images"]}
    analyzed = {}
    for content_hash, asset in assets.items():
        candidates = [
            item for item in topics["ai-analysis-results"]
            if item.get("scenario_id") == SCENARIO_ID
            and item.get("content_hash") == content_hash
            and item.get("status") == "analyzed"
        ]
        analyzed[content_hash] = min(candidates, key=lambda item: item["_kafka_timestamp_ms"])

    yolo = []
    transport_latencies = []
    for content_hash, asset in assets.items():
        result = analyzed[content_hash]
        detections = result.get("detections") or []
        source_candidates = [
            item for item in topics["drone-video"]
            if item.get("content_hash") == content_hash
            and item["_kafka_timestamp_ms"] <= result["_kafka_timestamp_ms"]
        ]
        source = max(source_candidates, key=lambda item: item["_kafka_timestamp_ms"])
        latency = result["_kafka_timestamp_ms"] - source["_kafka_timestamp_ms"]
        transport_latencies.append(latency)
        classes = {}
        for detection in detections:
            classes[detection["class_name"]] = classes.get(detection["class_name"], 0) + 1
        yolo.append({
            "asset_id": asset["asset_id"],
            "frame_id": result["frame_id"],
            "real_image_sha256": content_hash,
            "official_annotations": asset["official_annotations"],
            "live_detection_count": len(detections),
            "live_class_counts": classes,
            "confidence_range": [
                round(min((item["confidence"] for item in detections), default=0.0), 6),
                round(max((item["confidence"] for item in detections), default=0.0), 6),
            ],
            "producer_to_ai_result_ms": latency,
            "gps_provenance": result.get("gps_provenance"),
            "declared_target_id": result.get("target_id"),
            "detections": [
                {
                    "class": item["class_name"],
                    "confidence": item["confidence"],
                    "bbox": item["bbox"],
                }
                for item in detections
            ],
        })

    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        for item in yolo:
            asset = next(value for value in assets.values() if value["asset_id"] == item["asset_id"])
            item["annotated_image"] = render_yolo_evidence(
                args.output.parent, asset, analyzed[asset["sha256"]]
            )

    state = initialize_state(
        str(SCENARIO / "gis" / "infrastructure.geojson"),
        allow_scenario_simulated_gps=True,
    )
    baseline = copy.deepcopy(state.graph)
    stage_graphs = [baseline]
    stage_names = ["baseline"]
    association_rows = []
    ordered_results = [analyzed[item["sha256"]] for item in selection["images"]]
    for index, result in enumerate(ordered_results):
        outcome = process_message(json.dumps(result).encode("utf-8"), state)
        for observation_id in outcome.accepted_observation_ids:
            record = state.observation_log.get(observation_id)
            association_rows.append({
                "frame_id": record.frame_id,
                "observation_id": record.observation_id,
                "matched_gis_source_id": record.matched_gis_source_id,
                "graph_node_id": record.node_id,
                "association_kind": record.association_kind,
                "association_distance_m": record.association_distance_m,
                "declared_target_id": record.declared_target_id,
                "gps_provenance": record.location_provenance,
            })
        if index == 0:
            stage_graphs.append(
                apply_observations(
                    baseline,
                    state.observation_log,
                    now=float(result["source_timestamp"]),
                )
            )
            stage_names.append("first_drone")
    all_drone = apply_observations(
        baseline,
        state.observation_log,
        now=max(float(item["source_timestamp"]) for item in ordered_results),
    )
    stage_graphs.append(all_drone)
    stage_names.append("all_drone")

    telemetry = json.loads((SCENARIO / "telemetry.json").read_text(encoding="utf-8"))["events"]
    degraded = apply_telemetry_event(all_drone, state.spatial_index.id_map, telemetry_event(telemetry[0]))
    recovered = apply_telemetry_event(degraded, state.spatial_index.id_map, telemetry_event(telemetry[1]))
    stage_graphs.extend((degraded, recovered))
    stage_names.extend(("telemetry_degraded", "recovery"))
    risk = predict_node_risk_report(stage_graphs)
    target_node = state.spatial_index.id_map["osm-way-791288888"]
    risk_stages = []
    for name, graph, snapshot in zip(stage_names, stage_graphs, risk["timeline"]):
        target_risk = snapshot["nodes"][target_node]
        risk_stages.append({
            "stage": name,
            "snapshot_timestamp": snapshot["snapshot_timestamp"],
            "estuary_road_state": state_row(graph, target_node),
            "estuary_road_risk": round(target_risk["failure_risk_score"], 6),
            "estuary_road_rank": target_risk["risk_rank"],
            "top_five": top_five(snapshot),
        })

    social_event = json.loads((SCENARIO / "social_events.json").read_text(encoding="utf-8"))["events"][0]
    social_wire = {
        "id": social_event["event_id"], "text": social_event["text"],
        "hazard": "flood", "event": SCENARIO_ID,
        "timestamp": epoch(social_event["scenario_timestamp"]), "source": "twitter",
        "data_type": "text", "gps": social_event["gps"],
        "target_ids": social_event["target_ids"],
    }
    gis_data = state.spatial_index.gis_data
    pending_graph = copy.deepcopy(baseline)
    confirmed_workflow = SocialAlertWorkflow(gis_data, pending_graph, state.spatial_index.id_map)
    confirmed_workflow.ingest_event(social_wire)
    pending_state = confirmed_workflow.dashboard_state()
    confirmed_workflow.decide("social-001", "confirm", responder_id="evaluation-responder")
    confirmed_state = confirmed_workflow.dashboard_state()
    rejected_workflow = SocialAlertWorkflow(gis_data, copy.deepcopy(baseline), state.spatial_index.id_map)
    rejected_workflow.ingest_event(social_wire)
    rejected_workflow.decide("social-001", "reject", responder_id="evaluation-responder")
    rejected_state = rejected_workflow.dashboard_state()

    satellite_results = [
        item for item in topics["satellite-change-results"]
        if item.get("scenario_id") == SCENARIO_ID
    ]
    satellite = max(satellite_results, key=lambda item: item["_kafka_timestamp_ms"])
    pair_inputs = [
        item for item in topics["satellite-imagery"]
        if item.get("scenario_id") == SCENARIO_ID
        and item["_kafka_timestamp_ms"] <= satellite["_kafka_timestamp_ms"]
    ]
    latest_by_phase = {
        phase: max(
            (item for item in pair_inputs if item.get("satellite_phase") == phase),
            key=lambda item: item["_kafka_timestamp_ms"],
        )
        for phase in ("pre", "post")
    }
    satellite_latency = satellite["_kafka_timestamp_ms"] - max(
        item["_kafka_timestamp_ms"] for item in latest_by_phase.values()
    )

    drone_scenario = [
        item for item in topics["drone-video"] if item.get("scenario_id") == SCENARIO_ID
    ]
    kafka_summary, kafka_error = kafka_counts()
    minio_summary, _, minio_error = minio_objects(1)
    report = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "scenario_id": SCENARIO_ID,
        "yolo_checkpoint_sha256": risk.get("checkpoint_manifest", {}).get("yolo_checkpoint_sha256"),
        "yolo": yolo,
        "deduplication": {
            "scenario_drone_events": len(drone_scenario),
            "duplicates": sum(bool(item.get("is_duplicate")) for item in drone_scenario),
            "methods": {
                method: sum(item.get("duplicate_method") == method for item in drone_scenario)
                for method in ("sha256", "phash")
            },
            "unique_minio_drone_objects": minio_summary.get("drone", {}).get("objects"),
            "p_hash_validation": validate_phash(selection["images"][0]),
        },
        "satellite_change": {
            "tile_id": satellite["tile_id"],
            "summary": satellite["summary"],
            "reference_labels": satellite["reference_labels"],
            "footprint": satellite["footprint"],
            "pair_to_result_ms": satellite_latency,
            "interpretation": satellite["interpretation"],
            "tgnn_integration": satellite["tgnn_integration"],
        },
        "gis_association": {
            "accepted_observations": len(association_rows),
            "by_frame": {
                frame: {
                    "observations": sum(item["frame_id"] == frame for item in association_rows),
                    "gis_source_ids": sorted({item["matched_gis_source_id"] for item in association_rows if item["frame_id"] == frame}),
                    "graph_node_ids": sorted({item["graph_node_id"] for item in association_rows if item["frame_id"] == frame}),
                    "association_kinds": sorted({item["association_kind"] for item in association_rows if item["frame_id"] == frame}),
                }
                for frame in sorted({item["frame_id"] for item in association_rows})
            },
            "trace_sample": association_rows[:3],
        },
        "graph_and_tgnn": {
            "nodes": len(baseline),
            "edges": baseline.number_of_edges(),
            "dependency_edges": sum(data.get("edge_type") == 1 for _, _, data in baseline.edges(data=True)),
            "checkpoint_sha256": risk["checkpoint_sha256"],
            "feature_order": risk["feature_order"],
            "calibrated_probability": risk["calibrated_probability"],
            "stages": risk_stages,
        },
        "cascade": cascade_fixture(),
        "social_workflow": {
            "pending": pending_state["counts"],
            "confirmed": confirmed_state["counts"],
            "confirmed_hotspots": len(confirmed_state["confirmed_hotspots"]["features"]),
            "confirmed_graph_observations": len(confirmed_state["graph_observations"]),
            "rejected": rejected_state["counts"],
            "rejected_hotspots": len(rejected_state["confirmed_hotspots"]["features"]),
            "rejected_graph_observations": len(rejected_state["graph_observations"]),
        },
        "latency_ms": {
            "producer_to_yolo": {
                "samples": len(transport_latencies),
                "values": transport_latencies,
                "median": statistics.median(transport_latencies),
            },
            "satellite_pair_to_change_result": satellite_latency,
            "spark": spark_latency(),
            "dashboard_api": api_latency(args.dashboard_url),
            "measurement_note": "Local single-machine measurements; Kafka tail APIs intentionally wait up to 1.5 seconds per topic.",
        },
        "event_counts": {
            "kafka": kafka_summary,
            "kafka_error": kafka_error,
            "minio": minio_summary,
            "minio_error": minio_error,
            "scenario_topic_records": {
                topic: sum(item.get("scenario_id") == SCENARIO_ID for item in rows)
                for topic, rows in topics.items()
            },
        },
    }
    # The YOLO identity belongs to its own committed artifact, not the TGNN manifest.
    manifest = json.loads((ROOT / "ai" / "computer_vision" / "artifact_manifest.json").read_text(encoding="utf-8"))
    report["yolo_checkpoint_sha256"] = manifest["sha256"]["artifacts/drone_detector/weights/best.pt"]
    report["yolo_training_metrics"] = manifest["final_metrics"]

    rendered = json.dumps(report, indent=2, sort_keys=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
