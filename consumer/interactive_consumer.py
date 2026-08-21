"""Interactively consume selected disaster topics and reconstruct images."""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

from common.object_storage import ObjectStorageClient, ObjectStorageError
from config.settings import configure_logging, settings


TOPIC_OPTIONS = {
    "1": "social-posts",
    "2": "drone-video",
    "3": "satellite-imagery",
    "5": "ai-analysis-results",
    "6": "gis-data",
}
logger = configure_logging("interactive_consumer")


def parse_topic_choice(choice: str) -> list[str]:
    """Validate a menu response and return unique topics in display order."""
    parts = [part.strip() for part in choice.split(",") if part.strip()]
    if parts == ["4"]:
        return list(TOPIC_OPTIONS.values())
    if not parts or "4" in parts or any(part not in TOPIC_OPTIONS for part in parts):
        raise ValueError("Choose 1, 2, 3, 5, 6, a combination, or 4 for all.")
    return list(dict.fromkeys(TOPIC_OPTIONS[part] for part in parts))


def prompt_for_topics() -> list[str]:
    print(
        "\nSelect topics:\n\n"
        "1. Social Media\n"
        "2. Drone Feed\n"
        "3. Satellite Imagery\n"
        "4. All\n"
        "5. AI Analysis Results\n"
        "6. GIS Imagery\n"
    )
    while True:
        try:
            return parse_topic_choice(input("Enter choice (example: 1 or 1,2): "))
        except ValueError as exc:
            print(f"Invalid selection: {exc}")


def _safe_filename(name: str, fallback: str) -> str:
    """Prevent path traversal and return a portable filename."""
    cleaned = Path(name).name.strip()
    return cleaned or fallback


def _timestamp(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value)).isoformat(timespec="seconds")
    except (TypeError, ValueError, OSError):
        return "unknown"


def display_social(event: dict[str, Any]) -> None:
    print(
        "\nSOCIAL DATA\n"
        f"Tweet ID: {event.get('id', 'unknown')}\n"
        f"Event: {event.get('event', 'unknown')}\n"
        f"Hazard: {event.get('hazard', 'unknown')}\n"
        f"Text: {event.get('text', '')}\n"
        f"Timestamp: {_timestamp(event.get('timestamp'))}\n"
    )


def save_base64_image(data: Any, destination: Path) -> None:
    if not isinstance(data, str) or not data:
        raise ValueError("image_data is missing or is not a Base64 string")
    try:
        image_bytes = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("image_data contains invalid Base64") from exc
    if not image_bytes:
        raise ValueError("decoded image is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(image_bytes)


def save_object_image(event: dict[str, Any], destination: Path) -> None:
    """Download an object by temporary URL, falling back to configured S3 credentials."""
    download_url = event.get("download_url")
    try:
        if isinstance(download_url, str) and download_url:
            with urlopen(download_url, timeout=60) as response:  # noqa: S310
                image_bytes = response.read(settings.max_message_bytes * 5)
        else:
            object_key = event.get("object_key")
            if not isinstance(object_key, str) or not object_key:
                raise ValueError("object_key is missing")
            image_bytes = ObjectStorageClient().download(object_key)
    except (URLError, TimeoutError, OSError, ObjectStorageError) as exc:
        object_key = event.get("object_key")
        if not isinstance(object_key, str) or not object_key:
            raise ValueError(f"Object download failed: {exc}") from exc
        try:
            image_bytes = ObjectStorageClient().download(object_key)
        except ObjectStorageError as fallback_exc:
            raise ValueError(f"Object download failed: {fallback_exc}") from fallback_exc
    if not image_bytes:
        raise ValueError("downloaded image is empty")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(image_bytes)


def save_transferred_image(event: dict[str, Any], destination: Path) -> None:
    if event.get("is_duplicate"):
        print(
            f"Duplicate image skipped: {destination.name} -> "
            f"canonical {event.get('canonical_image_id', 'unknown')} "
            f"({event.get('duplicate_method', 'unknown')})"
        )
        return
    mode = event.get("transfer_mode", "base64")
    if mode == "base64":
        save_base64_image(event.get("image_data"), destination)
    elif mode == "object_storage":
        save_object_image(event, destination)
    else:
        raise ValueError(f"Unsupported transfer_mode: {mode}")


def handle_drone(event: dict[str, Any]) -> None:
    name = _safe_filename(str(event.get("frame_id", "")), "drone_frame.jpg")
    destination = settings.received_drone_dir / name
    save_transferred_image(event, destination)
    if not event.get("is_duplicate"):
        print(f"Drone image received: {destination.name}")


def handle_satellite(event: dict[str, Any]) -> None:
    original = _safe_filename(str(event.get("image_id", "")), "satellite_image.tif")
    destination = settings.received_satellite_dir / f"{Path(original).stem}.jpg"
    save_transferred_image(event, destination)
    if not event.get("is_duplicate"):
        print(f"Satellite image received: {destination.name}")


def handle_gis(event: dict[str, Any]) -> None:
    name = _safe_filename(str(event.get("map_id", "")), "gis_map.jpg")
    destination = settings.received_gis_dir / name
    save_transferred_image(event, destination)
    if not event.get("is_duplicate"):
        print(f"GIS image received: {destination.name}")


def dispatch(topic: str, event: dict[str, Any]) -> None:
    if topic == "social-posts":
        display_social(event)
    elif topic == "drone-video":
        handle_drone(event)
    elif topic == "satellite-imagery":
        handle_satellite(event)
    elif topic == "ai-analysis-results":
        print("\nAI ANALYSIS\n" + json.dumps(event, indent=2) + "\n")
    elif topic == "gis-data":
        handle_gis(event)
    else:
        logger.warning("Ignoring unexpected topic %s", topic)


def main() -> None:
    topics = prompt_for_topics()
    logger.info("Connecting to %s; topics=%s", settings.kafka_bootstrap_servers, topics)
    try:
        consumer = KafkaConsumer(
            *topics,
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            group_id="disaster-interactive-consumer",
            auto_offset_reset="latest",
            enable_auto_commit=True,
            max_partition_fetch_bytes=settings.max_message_bytes,
            fetch_max_bytes=settings.max_message_bytes * 3,
        )
    except NoBrokersAvailable as exc:
        logger.error(
            "Kafka is unavailable at %s. Check IP, advertised listener, and TCP 9092.",
            settings.kafka_bootstrap_servers,
        )
        raise SystemExit(1) from exc

    print(f"\nListening to: {', '.join(topics)} (Ctrl+C to stop)\n")
    try:
        for message in consumer:
            try:
                event = json.loads(message.value.decode("utf-8"))
                if not isinstance(event, dict):
                    raise ValueError("JSON message is not an object")
                dispatch(message.topic, event)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                logger.error(
                    "Bad message topic=%s partition=%s offset=%s: %s",
                    message.topic,
                    message.partition,
                    message.offset,
                    exc,
                )
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
