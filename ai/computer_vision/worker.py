"""Consume drone images, run preprocessing + YOLO, and publish AI results."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
from pathlib import Path
from typing import Any

from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

from ai.computer_vision.contracts import RESULT_TOPIC, result_event
from ai.computer_vision.detector import DamageDetector
from ai.preprocessing import PreprocessingPipeline
from common.kafka_utils import create_json_producer, send_event
from common.object_storage import ObjectStorageClient
from config.settings import configure_logging, settings


logger = configure_logging("ai_worker")


def image_bytes(event: dict[str, Any], storage: ObjectStorageClient | None = None) -> bytes:
    mode = event.get("transfer_mode", "base64")
    if mode == "base64":
        payload = event.get("image_data")
        if not isinstance(payload, str) or not payload:
            raise ValueError("Base64 event has no image_data")
        try:
            return base64.b64decode(payload, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise ValueError("image_data is not valid Base64") from exc
    if mode == "object_storage":
        key = event.get("object_key")
        if not isinstance(key, str) or not key:
            raise ValueError("Object-storage event has no object_key")
        return (storage or ObjectStorageClient()).download(key)
    if mode == "local_path":
        raw_path = event.get("image_path")
        if not settings.ai_allow_local_paths or not isinstance(raw_path, str):
            raise ValueError("Legacy local_path events are disabled; set AI_ALLOW_LOCAL_PATHS=true")
        return Path(raw_path).expanduser().read_bytes()
    raise ValueError(f"Unsupported transfer_mode: {mode}")


def build_pipeline() -> PreprocessingPipeline:
    return PreprocessingPipeline(
        image_size=settings.ai_image_size,
        enable_motion=settings.ai_enable_motion_filter,
        enable_quality=settings.ai_enable_quality_filter,
        enable_near_dedup=settings.ai_enable_near_dedup,
        motion_threshold=settings.ai_motion_threshold,
        blur_threshold=settings.ai_blur_threshold,
        phash_threshold=settings.ai_phash_threshold,
        phash_window=settings.ai_phash_window,
    )


def run(max_events: int | None = None) -> int:
    detector = DamageDetector(
        settings.resolved_ai_model_path,
        settings.ai_confidence_threshold,
        settings.ai_image_size,
        settings.ai_device,
    )
    pipeline = build_pipeline()
    try:
        consumer = KafkaConsumer(
            "drone-video",
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            group_id=settings.ai_consumer_group,
            auto_offset_reset=settings.ai_starting_offsets,
            enable_auto_commit=True,
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            max_partition_fetch_bytes=settings.max_message_bytes,
        )
        producer = create_json_producer()
    except NoBrokersAvailable as exc:
        raise ConnectionError(f"Kafka unavailable at {settings.kafka_bootstrap_servers}") from exc
    storage: ObjectStorageClient | None = None
    processed = 0
    try:
        for message in consumer:
            event = message.value
            if not isinstance(event, dict):
                logger.warning("Skipping non-object event at offset %s", message.offset)
                continue
            if event.get("is_duplicate"):
                output = result_event(event, "duplicate_skipped", reason="producer_deduplication")
            else:
                try:
                    if event.get("transfer_mode") == "object_storage" and storage is None:
                        storage = ObjectStorageClient()
                    prepared = pipeline.process_bytes(image_bytes(event, storage))
                    if not prepared.accepted:
                        output = result_event(
                            event, "filtered", preprocessing=prepared.metadata, reason=prepared.reason
                        )
                    else:
                        detections = detector.predict([prepared.image])[0]
                        output = result_event(
                            event, "analyzed", detections=detections, preprocessing=prepared.metadata
                        )
                except Exception as exc:  # keep a bad frame from stopping the stream
                    logger.exception("AI processing failed for %s", event.get("frame_id"))
                    output = result_event(event, "error", reason=str(exc))
            if send_event(producer, RESULT_TOPIC, output, logger):
                processed += 1
                logger.info(
                    "AI result frame=%s status=%s detections=%s",
                    output["frame_id"], output["status"], output["detection_count"],
                )
            if max_events is not None and processed >= max_events:
                break
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        producer.flush(timeout=30)
        producer.close()
        consumer.close()
    return processed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Stop after N published results")
    args = parser.parse_args()
    try:
        logger.info("AI worker stopped after %d results", run(args.limit))
    except (ConnectionError, FileNotFoundError, RuntimeError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
