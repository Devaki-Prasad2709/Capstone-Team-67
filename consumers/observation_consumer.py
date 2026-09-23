"""Thin ai-analysis-results -> existing ObservationLog -> G(t) adapter.

Run from the repo root: python -m consumers.observation_consumer --help
GIS_DATASET_PATH (or --gis-path) must name a GeoJSON file, not map imagery.
Real geolocation is unavailable: normal CLI startup stops before Kafka.
Only explicit --test-manual-location LAT LON enables the existing test
geolocator; every detection then uses that caller-supplied test location.

Graph and observations are in memory. Kafka auto-commit follows the existing
interactive consumer, so restarting does not restore graph/observation state.
No TGNN, dashboard, producer, satellite or NLP integration is performed here.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from dataclasses import dataclass
from pathlib import Path

import networkx as nx
from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

from ai.computer_vision.contracts import RESULT_TOPIC
from config.settings import configure_logging, settings
from core.gis.building_association import SpatialIndex, build_building_lookup
from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.observation.geolocator import GeoLocator, ManualOverrideGeoLocator, NullGeoLocator
from core.observation.ingest import IngestResult, ingest_ai_analysis_result
from core.observation.observation_log import ObservationLog
from core.observation.state_update import apply_observations

logger = logging.getLogger("observation_consumer")


@dataclass
class ObservationState:
    graph: nx.DiGraph
    spatial_index: SpatialIndex
    observation_log: ObservationLog
    geolocator: GeoLocator
    drone_telemetry: dict | None = None


def initialize_state(gis_path: str, test_manual_location=None) -> ObservationState:
    """Load structural GIS once; manual coordinates require explicit opt-in."""
    if not gis_path.strip():
        raise ValueError("Set GIS_DATASET_PATH or --gis-path to a GeoJSON file.")
    path = Path(gis_path).expanduser()
    if not path.is_absolute():
        path = settings.project_root / path
    if not path.is_file():
        raise ValueError(f"GIS path must be a GeoJSON file, not an imagery directory: {path}")

    geolocator = NullGeoLocator()
    telemetry = None
    if test_manual_location is not None:
        lat, lon = test_manual_location
        if not (math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180):
            raise ValueError("Test latitude/longitude must be finite WGS84 coordinates.")
        geolocator = ManualOverrideGeoLocator()
        telemetry = {"override_lat": lat, "override_lon": lon}
        logger.warning("TEST ONLY: every detection will use manual latitude=%s longitude=%s", lat, lon)

    gis_data = load_gis(str(path))
    graph, id_map = build_graph_from_gis(gis_data, seed=42)
    lookup = build_building_lookup(gis_data, id_map)
    state = ObservationState(
        graph, SpatialIndex(gis_data, lookup, id_map), ObservationLog(), geolocator, telemetry,
    )
    logger.info("GIS initialized: nodes=%s edges=%s excluded_infra=%s demo=%s",
                len(graph), graph.number_of_edges(), gis_data.excluded_infra_count, gis_data.is_demo_fixture())
    return state


def _finite_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _validate_event(event) -> None:
    """Validate wire shape before ingestion can append any detections.

    No class remapping, geolocation, association or damage rules live here.
    The decoded event is never changed.
    """
    if not isinstance(event, dict):
        raise ValueError("JSON message is not an object")
    if not isinstance(event.get("frame_id"), str) or not event["frame_id"]:
        raise ValueError("frame_id must be a nonempty string")
    if not isinstance(event.get("status"), str) or not event["status"]:
        raise ValueError("status must be a nonempty string")
    detections = event.get("detections")
    if not isinstance(detections, list):
        raise ValueError("detections must be a list")
    if event["status"] == "analyzed" and detections:
        timestamp = event.get("source_timestamp") or event.get("processed_at")
        try:
            valid_time = not isinstance(timestamp, bool) and math.isfinite(float(timestamp))
        except (TypeError, ValueError, OverflowError):
            valid_time = False
        if not valid_time:
            raise ValueError("Analyzed detections require a finite epoch source_timestamp or processed_at")
    for entry in detections:
        if not isinstance(entry, dict):
            raise ValueError("Each detection must be an object")
        if type(entry.get("class_id")) is not int or not isinstance(entry.get("class_name"), str):
            raise ValueError("Detection requires integer class_id and string class_name")
        confidence = entry.get("confidence")
        if not _finite_number(confidence) or not 0 <= confidence <= 1:
            raise ValueError("Detection confidence must be finite and between 0 and 1")
        bbox = entry.get("bbox")
        if not isinstance(bbox, list) or len(bbox) != 4 or not all(_finite_number(v) for v in bbox):
            raise ValueError("Detection bbox must contain four finite numbers")


def process_message(value: bytes, state: ObservationState) -> IngestResult:
    """Decode one message; delegate evidence and graph updates to core APIs.

    Raises on invalid input or processing failure; consume_messages logs it
    and continues. Core ingestion is append-only, not transactional: a runtime
    failure after an earlier detection may leave that observation in the log.
    """
    event = json.loads(value.decode("utf-8"))
    _validate_event(event)
    result = ingest_ai_analysis_result(
        event, state.geolocator, state.spatial_index, state.observation_log,
        drone_telemetry=state.drone_telemetry,
    )
    if result.detections_ingested:
        state.graph = apply_observations(state.graph, state.observation_log)
    logger.info(
        "frame_id=%s detections_seen=%s detections_ingested=%s "
        "detections_skipped_no_node=%s touched_node_ids=%s",
        result.frame_id, result.detections_seen, result.detections_ingested,
        result.detections_skipped_no_node, result.touched_node_ids,
    )
    return result


def consume_messages(consumer, state: ObservationState) -> None:
    """Works with KafkaConsumer or an in-memory iterable of message objects."""
    for message in consumer:
        try:
            process_message(message.value, state)
        except Exception:
            logger.exception(
                "Message processing failed topic=%s partition=%s offset=%s; continuing. "
                "Earlier detections may already be in the append-only log.",
                message.topic, message.partition, message.offset,
            )


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gis-path", default=settings.gis_dataset_path,
                        help="GeoJSON file; defaults to GIS_DATASET_PATH")
    parser.add_argument("--test-manual-location", nargs=2, type=float, metavar=("LAT", "LON"),
                        help="TEST ONLY: explicitly assign this location to every detection")
    args = parser.parse_args(argv)
    configure_logging("observation_consumer")
    try:
        state = initialize_state(args.gis_path, args.test_manual_location)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.error("Initialization failed: %s", exc)
        raise SystemExit(1) from exc
    if isinstance(state.geolocator, NullGeoLocator):
        logger.error("Real geolocation is unavailable. Stopping before Kafka; no GPS will be fabricated. "
                     "For an explicit development test only, use --test-manual-location LAT LON.")
        raise SystemExit(1)

    logger.info("Connecting to %s; topic=%s (TEST ONLY)", settings.kafka_bootstrap_servers, RESULT_TOPIC)
    try:
        consumer = KafkaConsumer(
            RESULT_TOPIC,
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            group_id="disaster-observation-test-v1",
            auto_offset_reset="latest",
            enable_auto_commit=True,
            max_partition_fetch_bytes=settings.max_message_bytes,
            fetch_max_bytes=settings.max_message_bytes * 3,
        )
    except NoBrokersAvailable as exc:
        logger.error("Kafka is unavailable at %s. Check IP, advertised listener, and TCP 9092.",
                     settings.kafka_bootstrap_servers)
        raise SystemExit(1) from exc
    try:
        consume_messages(consumer, state)
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
