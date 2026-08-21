"""Stream deduplicated ISBDA images via Base64 or object references."""

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


TOPIC = "drone-video"
logger = configure_logging("drone_producer")


def discover_images(dataset_dir: Path) -> list[Path]:
    extensions = {".jpg", ".jpeg"}
    return sorted(path for path in dataset_dir.rglob("*") if path.suffix.lower() in extensions)


def build_event(
    image_path: Path,
    deduplicator: ImageDeduplicator,
    transfer: ImageTransfer,
) -> dict[str, object]:
    image_bytes = image_path.read_bytes()
    decoded = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    result = deduplicator.check_and_register(
        "drone", image_path.name, image_bytes, decoded
    )
    event: dict[str, object] = {
        "frame_id": image_path.name,
        "timestamp": time.time(),
        "source": "drone",
        "data_type": "image",
        "format": image_path.suffix.lower().lstrip("."),
    }
    if result.is_duplicate:
        event.update(
            duplicate_event_fields(
                result.canonical_id,
                result.content_hash,
                result.canonical_content_hash,
                result.duplicate_method,
                result.perceptual_distance,
            )
        )
        return event
    return transfer.attach(
        event,
        image_bytes,
        "drone",
        image_path.name,
        result.content_hash,
        "image/jpeg",
    )


def run(dataset_path: str, delay: float, limit: int | None = None) -> int:
    dataset_dir = require_directory(dataset_path, "DRONE_DATASET_PATH")
    images = discover_images(dataset_dir)
    if not images:
        raise FileNotFoundError(f"No JPG/JPEG images found under {dataset_dir}")
    deduplicator = ImageDeduplicator()
    transfer = ImageTransfer()
    producer = create_json_producer()
    sent = 0
    try:
        for image_path in images:
            try:
                event = build_event(image_path, deduplicator, transfer)
                estimated_bytes = len(json.dumps(event).encode("utf-8"))
                if estimated_bytes >= settings.max_message_bytes:
                    logger.warning(
                        "Skipping %s: Kafka event is %.2f MiB (limit %.2f MiB)",
                        image_path.name,
                        estimated_bytes / 1048576,
                        settings.max_message_bytes / 1048576,
                    )
                    if not event.get("is_duplicate"):
                        deduplicator.unregister_canonical("drone", image_path.name)
                    continue
                delivered = send_event(producer, TOPIC, event, logger)
                if delivered:
                    sent += 1
                    if event.get("is_duplicate"):
                        logger.info(
                            "Skipped duplicate bytes for %s; canonical=%s method=%s",
                            image_path.name,
                            event.get("canonical_image_id"),
                            event.get("duplicate_method"),
                        )
                    else:
                        logger.info(
                            "Sent drone frame %s via %s",
                            image_path.name,
                            event.get("transfer_mode"),
                        )
                elif not event.get("is_duplicate"):
                    deduplicator.unregister_canonical("drone", image_path.name)
            except (OSError, cv2.error, ObjectStorageError) as exc:
                deduplicator.unregister_canonical("drone", image_path.name)
                logger.error("Could not process %s: %s", image_path, exc)
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
    parser.add_argument("--dataset", default=settings.drone_dataset_path)
    parser.add_argument("--delay", type=float, default=settings.drone_delay)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    try:
        logger.info("Completed after sending %d images", run(args.dataset, args.delay, args.limit))
    except (FileNotFoundError, ConnectionError, ObjectStorageError, ValueError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
