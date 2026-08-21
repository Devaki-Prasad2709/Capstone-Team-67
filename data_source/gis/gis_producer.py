"""Replay GIS map imagery through the same deduplicated MinIO/Kafka contract."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from common.deduplication import ImageDeduplicator
from common.image_transfer import ImageTransfer, duplicate_event_fields
from common.kafka_utils import create_json_producer, send_event
from common.object_storage import ObjectStorageError
from config.settings import configure_logging, require_directory, settings


TOPIC = "gis-data"
logger = configure_logging("gis_producer")


def discover_images(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"})


def build_event(path: Path, deduplicator: ImageDeduplicator, transfer: ImageTransfer) -> dict[str, object]:
    data = path.read_bytes()
    image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    result = deduplicator.check_and_register("gis", path.name, data, image)
    event: dict[str, object] = {
        "map_id": path.name, "timestamp": time.time(), "source": "gis",
        "data_type": "image", "format": path.suffix.lower().lstrip("."),
    }
    if result.is_duplicate:
        event.update(duplicate_event_fields(
            result.canonical_id, result.content_hash, result.canonical_content_hash,
            result.duplicate_method, result.perceptual_distance,
        ))
        return event
    content_type = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return transfer.attach(event, data, "gis", path.name, result.content_hash, content_type)


def run(dataset_path: str, delay: float, limit: int | None = None) -> int:
    folder = require_directory(dataset_path, "GIS_DATASET_PATH")
    images = discover_images(folder)
    if not images:
        raise FileNotFoundError(f"No JPG/JPEG/PNG images found under {folder}")
    deduplicator, transfer, producer = ImageDeduplicator(), ImageTransfer(), create_json_producer()
    sent = 0
    try:
        for path in images:
            try:
                event = build_event(path, deduplicator, transfer)
                if len(json.dumps(event).encode("utf-8")) >= settings.max_message_bytes:
                    logger.warning("Skipping oversized GIS event %s", path.name)
                    if not event.get("is_duplicate"):
                        deduplicator.unregister_canonical("gis", path.name)
                    continue
                if send_event(producer, TOPIC, event, logger):
                    sent += 1
            except (OSError, cv2.error, ObjectStorageError) as exc:
                deduplicator.unregister_canonical("gis", path.name)
                logger.error("Could not process %s: %s", path, exc)
            if limit is not None and sent >= limit:
                break
            time.sleep(max(delay, 0))
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        producer.flush(timeout=30)
        producer.close()
    return sent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", default=settings.gis_dataset_path)
    parser.add_argument("--delay", type=float, default=settings.gis_delay)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    try:
        logger.info("Completed after sending %d GIS images", run(args.dataset, args.delay, args.limit))
    except (FileNotFoundError, ConnectionError, ObjectStorageError, ValueError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
