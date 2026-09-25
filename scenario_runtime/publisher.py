"""Build and publish frozen scenario inputs through production contracts."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable

from common.deduplication import ImageDeduplicator
from common.image_transfer import ImageTransfer
from common.kafka_utils import create_json_producer, send_event
from common.topics import DRONE_TOPIC, SATELLITE_TOPIC, SOCIAL_TOPIC, TELEMETRY_TOPIC
from config.settings import settings
from core.observation.telemetry import validate_telemetry_event
from data_source.drone.drone_producer import build_event as build_drone_event
from data_source.satellite.satellite_producer import build_event as build_satellite_event
from data_source.social.social_producer import standardize_record


logger = logging.getLogger(__name__)


def _read_json(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _epoch(timestamp: str) -> float:
    return datetime.fromisoformat(timestamp.replace("Z", "+00:00")).timestamp()


class ScenarioEventFactory:
    """Translate frozen assets into the same wire schemas as field producers."""

    def __init__(
        self,
        scenario_path: Path,
        *,
        spacenet_root: Path,
        isbda_root: Path,
        deduplicator: ImageDeduplicator | None = None,
        transfer: ImageTransfer | None = None,
        drone_builder: Callable = build_drone_event,
        satellite_builder: Callable = build_satellite_event,
    ) -> None:
        self.scenario_path = Path(scenario_path).resolve()
        self.scenario_dir = self.scenario_path.parent
        self.scenario = _read_json(self.scenario_path)
        self.scenario_id = self.scenario["scenario_id"]
        self.spacenet_root = Path(spacenet_root).resolve()
        self.isbda_root = Path(isbda_root).resolve()
        self.deduplicator = deduplicator or ImageDeduplicator()
        self.transfer = transfer or ImageTransfer()
        self._drone_builder = drone_builder
        self._satellite_builder = satellite_builder

        self.satellite = _read_json(self.scenario_dir / "satellite" / "pair.json")
        selection = _read_json(self.scenario_dir / "drone" / "selection.json")
        self.drone_assets = {item["asset_id"]: item for item in selection["images"]}
        self.drone_events = {
            item["event_id"]: item
            for item in _read_json(self.scenario_dir / "drone_events.json")["events"]
        }
        self.social_events = {
            item["event_id"]: item
            for item in _read_json(self.scenario_dir / "social_events.json")["events"]
        }
        self.telemetry_events = {
            item["event_id"]: item
            for item in _read_json(self.scenario_dir / "telemetry.json")["events"]
        }

    def _provenance(self, item: dict) -> dict:
        return {
            "scenario_id": self.scenario_id,
            "scenario_event_id": item["event_id"],
            "scenario_timestamp": item["scenario_timestamp"],
            "input_origin": item["input_origin"],
            "simulation_fields": item["simulation_fields"],
        }

    def drone(self, event_id: str) -> dict:
        definition = self.drone_events[event_id]
        asset = self.drone_assets[definition["asset_id"]]
        image_path = self.isbda_root / asset["relative_path"]
        if not image_path.is_file():
            raise FileNotFoundError(f"Scenario drone image is missing: {image_path}")
        event = self._drone_builder(image_path, self.deduplicator, self.transfer)
        event.update(self._provenance(definition))
        event.update(
            {
                "timestamp": _epoch(definition["scenario_timestamp"]),
                "asset_id": definition["asset_id"],
                "target_id": definition["target_id"],
                "gps": definition["gps"],
                "annotation_primary_class": definition["annotation_primary_class"],
            }
        )
        return event

    def satellite_pair(self) -> list[dict]:
        events = []
        for phase in ("pre_event", "post_event"):
            definition = self.satellite[phase]
            image_path = self.spacenet_root / definition["relative_path"]
            if not image_path.is_file():
                raise FileNotFoundError(f"Scenario satellite image is missing: {image_path}")
            event = self._satellite_builder(
                image_path,
                settings.satellite_jpeg_quality,
                self.deduplicator,
                self.transfer,
            )
            if event is None:
                raise ValueError(f"Could not decode scenario satellite image: {image_path}")
            event.update(
                {
                    "timestamp": _epoch(definition["scenario_timestamp"]),
                    "scenario_id": self.scenario_id,
                    "scenario_event_id": f"satellite-{phase.replace('_event', '')}",
                    "scenario_timestamp": definition["scenario_timestamp"],
                    "satellite_phase": phase.replace("_event", ""),
                    "tile_id": self.satellite["tile_id"],
                    "bbox": definition["bbox"],
                    "input_origin": "real-spacenet8",
                    "simulation_fields": ["scenario_timestamp"],
                    "reference_feature_count": self.satellite["reference"]["feature_count"],
                    "reference_flooded_feature_count": self.satellite["reference"]["flooded_feature_count"],
                }
            )
            events.append(event)
        return events

    def social(self, event_id: str) -> dict:
        definition = self.social_events[event_id]
        event = standardize_record(
            {
                "tweet_id": definition["event_id"],
                "tweet_text": definition["text"],
                "label": "flood",
                "event_name": self.scenario_id,
            }
        )
        if event is None:  # The frozen definition is validated, but keep this boundary strict.
            raise ValueError(f"Invalid frozen social event: {event_id}")
        event.update(self._provenance(definition))
        event.update(
            {
                "timestamp": _epoch(definition["scenario_timestamp"]),
                "gps": definition["gps"],
                "target_ids": definition["target_ids"],
                "review_status": "PENDING_REVIEW",
            }
        )
        return event

    def telemetry(self, event_id: str) -> dict:
        definition = self.telemetry_events[event_id]
        event = {
            "id": definition["event_id"],
            "timestamp": _epoch(definition["scenario_timestamp"]),
            "target_id": definition["target_id"],
            "load": definition["load"],
            "capacity": definition["capacity"],
            "damage": definition["damage"],
            "event_type": definition["event_type"],
            "source": "infrastructure_telemetry",
            "data_type": "telemetry",
            **self._provenance(definition),
        }
        validate_telemetry_event(event)
        return event

    def rollback_image_registration(self, event: dict) -> None:
        """Match normal producer behavior when a newly registered send fails."""
        if event.get("is_duplicate"):
            return
        source = event.get("source")
        canonical_id = event.get("frame_id") if source == "drone" else event.get("image_id")
        if source in {"drone", "satellite"} and isinstance(canonical_id, str):
            self.deduplicator.unregister_canonical(source, canonical_id)


class ScenarioKafkaDispatcher:
    """Route input milestones to Kafka; derived milestones remain consumer work."""

    ROUTES = {
        "satellite_post_event_change_detected": (SATELLITE_TOPIC, "satellite_pair", None),
        "first_drone_observation_arrives": (DRONE_TOPIC, "drone", "drone-001"),
        "severe_damage_observations_arrive": (DRONE_TOPIC, "drone", ("drone-002", "drone-003")),
        "social_distress_report_arrives_pending": (SOCIAL_TOPIC, "social", "social-001"),
        "road_telemetry_degrades": (TELEMETRY_TOPIC, "telemetry", "telemetry-001"),
        "recovery_information_arrives": (TELEMETRY_TOPIC, "telemetry", "telemetry-002"),
    }

    def __init__(
        self,
        factory: ScenarioEventFactory,
        *,
        producer=None,
        sender: Callable = send_event,
    ) -> None:
        self.factory = factory
        self.producer = producer or create_json_producer()
        self._sender = sender

    def __call__(self, timeline_event: dict) -> None:
        route = self.ROUTES.get(timeline_event["name"])
        if route is None:
            logger.info(
                "Timeline milestone %s has no injected output; downstream services derive it",
                timeline_event["event_id"],
            )
            return
        topic, method_name, event_ids = route
        build = getattr(self.factory, method_name)
        if event_ids is None:
            payloads = build()
        elif isinstance(event_ids, tuple):
            payloads = [build(event_id) for event_id in event_ids]
        else:
            payloads = [build(event_ids)]
        for payload in payloads:
            try:
                encoded_size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                if encoded_size >= settings.max_message_bytes:
                    raise ValueError(
                        f"Scenario payload for {topic} is {encoded_size} bytes, exceeding "
                        f"KAFKA_MAX_MESSAGE_BYTES={settings.max_message_bytes}"
                    )
                if not self._sender(self.producer, topic, payload, logger):
                    raise RuntimeError(f"Kafka delivery failed for {topic}")
            except Exception:
                rollback = getattr(self.factory, "rollback_image_registration", None)
                if rollback is not None:
                    rollback(payload)
                raise
            logger.info("Published %s to %s", payload.get("scenario_event_id"), topic)

    def close(self) -> None:
        self.producer.flush(timeout=30)
        self.producer.close()
