"""Consume satellite pairs and publish separate broad-area change results."""

from __future__ import annotations

import argparse
import base64
import binascii
import json

from kafka import KafkaConsumer
from kafka.errors import NoBrokersAvailable

from common.kafka_utils import create_json_producer, send_event
from common.object_storage import ObjectStorageClient
from common.topics import SATELLITE_CHANGE_TOPIC, SATELLITE_TOPIC
from config.settings import configure_logging, settings
from core.satellite.change_contract import SatellitePairAccumulator


logger = configure_logging("satellite_change")


class TransportImageLoader:
    def __init__(self) -> None:
        self._storage: ObjectStorageClient | None = None

    def __call__(self, event: dict) -> bytes:
        mode = event.get("transfer_mode", "base64")
        if mode == "base64":
            payload = event.get("image_data")
            if not isinstance(payload, str) or not payload:
                raise ValueError("Base64 satellite event has no image_data")
            try:
                return base64.b64decode(payload, validate=True)
            except (ValueError, binascii.Error) as exc:
                raise ValueError("Satellite image_data is not valid Base64") from exc
        if mode == "object_storage":
            key = event.get("object_key")
            if not isinstance(key, str) or not key:
                raise ValueError("Object-storage satellite event has no object_key")
            if self._storage is None:
                self._storage = ObjectStorageClient()
            return self._storage.download(key)
        raise ValueError(f"Unsupported satellite transfer_mode: {mode}")


def run(max_results: int | None = None) -> int:
    try:
        consumer = KafkaConsumer(
            SATELLITE_TOPIC,
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            group_id=settings.satellite_change_consumer_group,
            auto_offset_reset=settings.satellite_change_starting_offsets,
            enable_auto_commit=True,
            value_deserializer=lambda value: json.loads(value.decode("utf-8")),
            max_partition_fetch_bytes=settings.max_message_bytes,
        )
        producer = create_json_producer()
    except NoBrokersAvailable as exc:
        raise ConnectionError(f"Kafka unavailable at {settings.kafka_bootstrap_servers}") from exc

    accumulator = SatellitePairAccumulator(
        TransportImageLoader(), grid_size=settings.satellite_change_grid_size
    )
    published = 0
    try:
        for message in consumer:
            try:
                result = accumulator.accept(message.value)
                if result is not None and send_event(producer, SATELLITE_CHANGE_TOPIC, result, logger):
                    published += 1
                    logger.info(
                        "Published broad-area change tile=%s changed_cells=%s",
                        result["tile_id"], result["summary"]["changed_cell_count"],
                    )
            except (KeyError, TypeError, ValueError, OSError) as exc:
                logger.error("Rejected satellite event offset=%s: %s", message.offset, exc)
            if max_results is not None and published >= max_results:
                break
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    finally:
        producer.flush(timeout=30)
        producer.close()
        consumer.close()
    return published


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None, help="Stop after N published results")
    args = parser.parse_args()
    try:
        logger.info("Satellite change worker stopped after %d results", run(args.limit))
    except (ConnectionError, RuntimeError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
