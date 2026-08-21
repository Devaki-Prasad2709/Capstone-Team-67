"""Deduplicate xBD TIFFs and transfer JPEGs via Kafka or object storage."""

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


TOPIC = "satellite-imagery"
logger = configure_logging("satellite_producer")


def discover_images(dataset_dir: Path) -> list[Path]:
    return sorted(
        path for path in dataset_dir.rglob("*") if path.suffix.lower() in {".tif", ".tiff"}
    )


def _to_uint8(image: np.ndarray) -> np.ndarray:
    if image.dtype == np.uint8:
        return image
    normalized = cv2.normalize(image, None, 0, 255, cv2.NORM_MINMAX)
    return normalized.astype(np.uint8)


def encode_tiff(image_path: Path, jpeg_quality: int) -> tuple[np.ndarray, bytes] | None:
    """Decode a TIFF and return the normalized image plus compressed JPEG bytes."""
    image = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
    if image is None:
        return None
    image = _to_uint8(image)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    elif image.ndim == 3 and image.shape[2] == 4:
        image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    ok, encoded = cv2.imencode(
        ".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, max(1, min(jpeg_quality, 100))]
    )
    if not ok:
        return None
    return image, encoded.tobytes()


def build_event(
    image_path: Path,
    jpeg_quality: int,
    deduplicator: ImageDeduplicator,
    transfer: ImageTransfer,
) -> dict[str, object] | None:
    encoded = encode_tiff(image_path, jpeg_quality)
    if encoded is None:
        return None
    image, jpeg_bytes = encoded
    result = deduplicator.check_and_register(
        "satellite", image_path.name, jpeg_bytes, image
    )
    event: dict[str, object] = {
        "image_id": image_path.name,
        "original_format": image_path.suffix.lower().lstrip("."),
        "stream_format": "jpg",
        "timestamp": time.time(),
        "source": "satellite",
        "data_type": "image",
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
        jpeg_bytes,
        "satellite",
        f"{image_path.stem}.jpg",
        result.content_hash,
        "image/jpeg",
    )


def run(dataset_path: str, delay: float, limit: int | None = None) -> int:
    dataset_dir = require_directory(dataset_path, "SATELLITE_DATASET_PATH")
    images = discover_images(dataset_dir)
    if not images:
        raise FileNotFoundError(f"No .tif/.tiff images found under {dataset_dir}")
    deduplicator = ImageDeduplicator()
    transfer = ImageTransfer()
    producer = create_json_producer()
    sent = 0
    try:
        for image_path in images:
            try:
                event = build_event(
                    image_path,
                    settings.satellite_jpeg_quality,
                    deduplicator,
                    transfer,
                )
                if event is None:
                    logger.warning("OpenCV could not decode/encode %s; skipping", image_path)
                    continue
                payload_size = len(json.dumps(event).encode("utf-8"))
                if payload_size >= settings.max_message_bytes:
                    logger.warning(
                        "Skipping %s: Kafka event is %.2f MiB (limit %.2f MiB)",
                        image_path.name,
                        payload_size / 1048576,
                        settings.max_message_bytes / 1048576,
                    )
                    if not event.get("is_duplicate"):
                        deduplicator.unregister_canonical("satellite", image_path.name)
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
                            "Sent satellite image %s via %s",
                            image_path.name,
                            event.get("transfer_mode"),
                        )
                elif not event.get("is_duplicate"):
                    deduplicator.unregister_canonical("satellite", image_path.name)
            except (OSError, cv2.error, ObjectStorageError) as exc:
                deduplicator.unregister_canonical("satellite", image_path.name)
                logger.error("Failed to process %s: %s", image_path, exc)
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
    parser.add_argument("--dataset", default=settings.satellite_dataset_path)
    parser.add_argument("--delay", type=float, default=settings.satellite_delay)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    try:
        logger.info("Completed after sending %d images", run(args.dataset, args.delay, args.limit))
    except (FileNotFoundError, ConnectionError, ObjectStorageError, ValueError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
