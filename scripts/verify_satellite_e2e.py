"""Run the real SpaceNet satellite path through Kafka, MinIO, and dashboard API.

This is an opt-in live integration check. It never inserts a derived result
into the dashboard; the dashboard reads the result emitted by the production
satellite change worker from Kafka.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from kafka import KafkaConsumer, TopicPartition
from shapely.geometry import shape

from common.object_storage import ObjectStorageClient
from common.topics import (
    AI_RESULTS_TOPIC,
    SATELLITE_CHANGE_TOPIC,
    SATELLITE_TOPIC,
    TELEMETRY_TOPIC,
)
from config.settings import settings


def _topic_offsets(topic: str) -> dict[int, int]:
    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id=None,
        enable_auto_commit=False,
        request_timeout_ms=5000,
        api_version_auto_timeout_ms=5000,
    )
    try:
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            raise RuntimeError(f"Kafka topic does not exist: {topic}")
        items = [TopicPartition(topic, partition) for partition in sorted(partitions)]
        return {item.partition: offset for item, offset in consumer.end_offsets(items).items()}
    finally:
        consumer.close()


def _read_range(topic: str, starts: dict[int, int]) -> list[dict]:
    consumer = KafkaConsumer(
        bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
        group_id=None,
        enable_auto_commit=False,
        request_timeout_ms=5000,
        api_version_auto_timeout_ms=5000,
    )
    try:
        items = [TopicPartition(topic, partition) for partition in sorted(starts)]
        consumer.assign(items)
        ends = consumer.end_offsets(items)
        for item in items:
            consumer.seek(item, starts[item.partition])
        records: list[dict] = []
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for item, messages in consumer.poll(timeout_ms=500).items():
                for message in messages:
                    if message.offset >= ends[item]:
                        continue
                    value = json.loads(message.value.decode("utf-8"))
                    records.append(
                        {
                            "topic": topic,
                            "partition": message.partition,
                            "offset": message.offset,
                            "value": value,
                        }
                    )
            if all(consumer.position(item) >= ends[item] for item in items):
                break
        return records
    finally:
        consumer.close()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _severity_is_coherent(score: float, severity: str) -> bool:
    expected = "low" if score < 0.15 else "moderate" if score < 0.40 else "severe"
    return severity == expected


def _http_json(base_url: str, path: str, *, method: str = "GET", payload=None) -> dict:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        base_url + path,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Dashboard API {method} {path} failed: {exc.code} {detail}") from exc


def _http_bytes(base_url: str, path: str) -> tuple[int, bytes]:
    with urlopen(base_url + path, timeout=30) as response:
        return response.status, response.read()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _wait_for_api(base_url: str, process: subprocess.Popen, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"Dashboard exited with code {process.returncode}")
        try:
            _http_json(base_url, "/api/scenario")
            return
        except (OSError, RuntimeError):
            time.sleep(0.2)
    raise TimeoutError("Dashboard API did not become ready")


def _wait_for_satellite_dispatch(base_url: str, timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    paused = False
    while time.monotonic() < deadline:
        status = _http_json(base_url, "/api/scenario")
        current = status.get("current_event") or {}
        if current.get("event_id") == "timeline-02-satellite" and not paused:
            _http_json(base_url, "/api/scenario/pause", method="POST", payload={})
            paused = True
        completed = {item["event_id"] for item in status.get("completed_events", [])}
        if "timeline-02-satellite" in completed:
            return status
        if status.get("state") == "failed":
            raise RuntimeError(f"Scenario runner failed: {status.get('error')}")
        time.sleep(0.05)
    raise TimeoutError("Scenario did not publish its satellite milestone in time")


def _wait_for_output(start_offsets: dict[int, int], worker: subprocess.Popen, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = _topic_offsets(SATELLITE_CHANGE_TOPIC)
        if any(current.get(partition, 0) > offset for partition, offset in start_offsets.items()):
            return current
        if worker.poll() not in (None, 0):
            raise RuntimeError(f"Satellite worker exited with code {worker.returncode}")
        time.sleep(0.25)
    raise TimeoutError("No satellite-change-results event arrived in time")


def run(output_path: Path) -> dict:
    if settings.image_transfer_mode != "object_storage":
        raise RuntimeError("Task 9 requires IMAGE_TRANSFER_MODE=object_storage")
    spacenet_root = Path(settings.spacenet8_dataset_path)
    if not spacenet_root.is_dir():
        raise RuntimeError(f"SpaceNet root is unavailable: {spacenet_root}")

    input_before = _topic_offsets(SATELLITE_TOPIC)
    output_before = _topic_offsets(SATELLITE_CHANGE_TOPIC)
    graph_input_offsets_before = {
        AI_RESULTS_TOPIC: _topic_offsets(AI_RESULTS_TOPIC),
        TELEMETRY_TOPIC: _topic_offsets(TELEMETRY_TOPIC),
    }
    gis_path = settings.project_root / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"
    model_path = settings.project_root / "tgnn/models/tgnn.pth"
    immutable_hashes_before = {
        "frozen_gis": _file_sha256(gis_path),
        "tgnn_checkpoint": _file_sha256(model_path),
    }
    worker_group = f"satellite-task9-{uuid.uuid4().hex[:12]}"
    worker_environment = os.environ.copy()
    worker_environment.update(
        {
            "SATELLITE_CHANGE_CONSUMER_GROUP": worker_group,
            "SATELLITE_CHANGE_STARTING_OFFSETS": "latest",
        }
    )
    worker_log_path = output_path.with_suffix(".worker.log")
    worker_log_path.parent.mkdir(parents=True, exist_ok=True)
    worker_log = worker_log_path.open("w", encoding="utf-8")
    worker = subprocess.Popen(
        [sys.executable, "-m", "core.satellite.change_worker", "--limit", "1"],
        cwd=settings.project_root,
        env=worker_environment,
        stdout=worker_log,
        stderr=subprocess.STDOUT,
        text=True,
    )

    dashboard_port = _free_port()
    dashboard_url = f"http://127.0.0.1:{dashboard_port}"
    dashboard_log_path = output_path.with_suffix(".dashboard.log")
    dashboard_log = dashboard_log_path.open("w", encoding="utf-8")
    dashboard = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "dashboard.server:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(dashboard_port),
        ],
        cwd=settings.project_root,
        env=os.environ.copy(),
        stdout=dashboard_log,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        _wait_for_api(dashboard_url, dashboard)
        time.sleep(2.0)
        if worker.poll() is not None:
            raise RuntimeError(f"Satellite worker failed to start (exit {worker.returncode})")

        _http_json(
            dashboard_url, "/api/scenario/speed", method="POST", payload={"speed": 10}
        )
        _http_json(dashboard_url, "/api/scenario/start", method="POST", payload={})
        scenario_status = _wait_for_satellite_dispatch(dashboard_url)
        output_after = _wait_for_output(output_before, worker)
        worker.wait(timeout=30)

        input_records = _read_range(SATELLITE_TOPIC, input_before)
        output_records = _read_range(SATELLITE_CHANGE_TOPIC, output_before)
        satellite_inputs = [
            item for item in input_records
            if item["value"].get("scenario_id") == "louisiana-east-flood-v1"
            and item["value"].get("tile_id") == "2_23_44"
        ]
        if len(satellite_inputs) != 2:
            raise AssertionError(f"Expected two new satellite inputs, got {len(satellite_inputs)}")
        output_candidates = [
            item for item in output_records
            if item["value"].get("scenario_id") == "louisiana-east-flood-v1"
            and item["value"].get("tile_id") == "2_23_44"
        ]
        if len(output_candidates) != 1:
            raise AssertionError(f"Expected one new satellite result, got {len(output_candidates)}")
        output_record = output_candidates[0]
        result = output_record["value"]

        dashboard_payload = _http_json(dashboard_url, "/api/satellite/change")
        dashboard_result = dashboard_payload.get("latest")
        if not dashboard_result or dashboard_result.get("id") != result.get("id"):
            raise AssertionError("Dashboard API did not expose the Kafka-produced result")

        storage = ObjectStorageClient()
        objects = []
        for phase in ("pre", "post"):
            source = result["source_images"][phase]
            raw_path = spacenet_root / source["source_relative_path"]
            if not raw_path.is_file():
                raise AssertionError(f"Original {phase} TIFF is no longer accessible")
            if _file_sha256(raw_path) != source["source_sha256"]:
                raise AssertionError(f"Original {phase} TIFF checksum differs from provenance")
            if raw_path.stat().st_size != source["source_size_bytes"]:
                raise AssertionError(f"Original {phase} TIFF size differs from provenance")
            key = source["object_key"]
            data = storage.download(key)
            if hashlib.sha256(data).hexdigest() != source["content_hash"]:
                raise AssertionError(f"MinIO {phase} object checksum differs from Kafka provenance")
            preview_status, preview_body = _http_bytes(
                dashboard_url, dashboard_result["source_images"][phase]["preview_url"]
            )
            if preview_body != data:
                raise AssertionError(f"Dashboard {phase} preview differs from MinIO object")
            objects.append(
                {
                    "phase": phase,
                    "key": key,
                    "uri": source["image_uri"],
                    "size_bytes": len(data),
                    "sha256": source["content_hash"],
                    "source_dataset": source["source_dataset"],
                    "source_relative_path": source["source_relative_path"],
                    "source_sha256": source["source_sha256"],
                    "source_size_bytes": source["source_size_bytes"],
                    "raw_source_accessible": True,
                    "dashboard_preview_status": preview_status,
                }
            )

        features = result["change"]["features"]
        scored = 0
        unknown = 0
        cell_bounds = []
        for feature in features:
            geometry = shape(feature["geometry"])
            if not geometry.is_valid or geometry.is_empty:
                raise AssertionError("Change grid contains invalid geometry")
            west, south, east, north = geometry.bounds
            if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
                raise AssertionError("Change grid geometry is outside WGS84 limits")
            cell_bounds.append(geometry.bounds)
            properties = feature["properties"]
            score = properties["diff_score"]
            if properties["valid_fraction"] < 0.95:
                unknown += 1
                if score is not None or properties["severity"] != "unknown":
                    raise AssertionError("Insufficient coverage was scored")
            else:
                scored += 1
                if not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise AssertionError("Scored cell does not have a finite difference score")
                if not _severity_is_coherent(float(score), properties["severity"]):
                    raise AssertionError("Severity label does not match disclosed thresholds")

        graph_input_offsets_after = {
            AI_RESULTS_TOPIC: _topic_offsets(AI_RESULTS_TOPIC),
            TELEMETRY_TOPIC: _topic_offsets(TELEMETRY_TOPIC),
        }
        immutable_hashes_after = {
            "frozen_gis": _file_sha256(gis_path),
            "tgnn_checkpoint": _file_sha256(model_path),
        }
        if graph_input_offsets_before != graph_input_offsets_after:
            raise AssertionError("Satellite processing published into graph/TGNN input topics")
        if immutable_hashes_before != immutable_hashes_after:
            raise AssertionError("Satellite processing modified frozen GIS or the TGNN checkpoint")
        if result.get("tgnn_integration") != "none":
            raise AssertionError("Satellite result claims a TGNN integration")
        if "node_id" in json.dumps(result, separators=(",", ":")):
            raise AssertionError("Satellite result contains a graph-node association")

        javascript = (settings.project_root / "dashboard/static/app.js").read_text(encoding="utf-8")
        if not all(
            marker in javascript
            for marker in (
                "ensureSatelliteMapLayer",
                "satellite-change-cells",
                "setMapData('satellite-change',result?.change",
            )
        ):
            raise AssertionError("Dashboard broad-area overlay binding is missing")

        source_events = []
        for record in sorted(
            satellite_inputs,
            key=lambda item: 0 if item["value"]["satellite_phase"] == "pre" else 1,
        ):
            value = record["value"]
            source_events.append(
                {
                    "scenario_event_id": value["scenario_event_id"],
                    "phase": value["satellite_phase"],
                    "image_id": value["image_id"],
                    "scenario_timestamp": value["scenario_timestamp"],
                    "input_origin": value["input_origin"],
                    "simulation_fields": value["simulation_fields"],
                    "source_dataset": value["source_dataset"],
                    "source_relative_path": value["source_relative_path"],
                    "source_sha256": value["source_sha256"],
                    "topic": record["topic"],
                    "partition": record["partition"],
                    "offset": record["offset"],
                    "object_key": value["object_key"],
                }
            )

        evidence = {
            "status": "passed",
            "executed_at_epoch": time.time(),
            "scenario_id": result["scenario_id"],
            "tile_id": result["tile_id"],
            "scenario_timestamp": result["scenario_timestamp"],
            "pipeline": [
                "scenario satellite producer",
                SATELLITE_TOPIC,
                "MinIO object references",
                "satellite change worker",
                "universal satellite normalizer",
                "generic broad-area classifier",
                SATELLITE_CHANGE_TOPIC,
                "dashboard /api/satellite/change",
                "WGS84 map overlay binding",
            ],
            "source_events": source_events,
            "minio_objects": objects,
            "result_event": {
                "id": result["id"],
                "topic": output_record["topic"],
                "partition": output_record["partition"],
                "offset": output_record["offset"],
                "feature_count": len(features),
                "scored_feature_count": scored,
                "unknown_feature_count": unknown,
                "geographic_bounds": list(shape(result["footprint"]).bounds),
                "summary": result["summary"],
                "output_crs": result["change"]["metadata"]["crs"],
                "normalizer_source": result["change"]["metadata"]["source"],
            },
            "dashboard": {
                "api_status": 200,
                "result_count": dashboard_payload["result_count"],
                "result_id": dashboard_result["id"],
                "preview_urls_resolved": True,
                "broad_area_overlay_binding": True,
                "direct_result_injection": False,
            },
            "isolation": {
                "tgnn_integration": result["tgnn_integration"],
                "graph_input_topic_offsets_before": graph_input_offsets_before,
                "graph_input_topic_offsets_after": graph_input_offsets_after,
                "immutable_hashes_before": immutable_hashes_before,
                "immutable_hashes_after": immutable_hashes_after,
                "result_contains_graph_node_id": False,
                "graph_and_tgnn_inputs_unchanged": True,
            },
            "coverage_policy": {
                "minimum_valid_fraction": result["change"]["metadata"]["min_valid_fraction"],
                "real_pair_insufficient_cells": unknown,
                "policy_verified_for_each_output_cell": True,
            },
            "kafka_offsets": {
                "input_before": input_before,
                "output_before": output_before,
                "output_after": output_after,
            },
            "worker": {"consumer_group": worker_group, "exit_code": worker.returncode},
            "scenario_runner": {
                "state": scenario_status["state"],
                "simulation_timestamp": scenario_status["simulation_timestamp"],
            },
        }
        output_path.write_text(json.dumps(evidence, indent=2) + "\n", encoding="utf-8")
        return evidence
    finally:
        if dashboard.poll() is None:
            dashboard.terminate()
            try:
                dashboard.wait(timeout=10)
            except subprocess.TimeoutExpired:
                dashboard.kill()
                dashboard.wait(timeout=10)
        dashboard_log.close()
        if worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=10)
        worker_log.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("results/satellite_e2e_2026-10-01.json"),
    )
    args = parser.parse_args()
    evidence = run(args.output.resolve())
    print(json.dumps(evidence, indent=2))


if __name__ == "__main__":
    main()
