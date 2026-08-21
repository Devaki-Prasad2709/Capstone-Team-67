"""Small Kafka helpers shared by data producers."""

from __future__ import annotations

import json
import logging
from typing import Any

from kafka import KafkaProducer
from kafka.errors import KafkaError, NoBrokersAvailable

from config.settings import settings


def create_json_producer() -> KafkaProducer:
    """Create a JSON producer with bounded message size and useful timeouts."""
    try:
        return KafkaProducer(
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            value_serializer=lambda value: json.dumps(value, ensure_ascii=False).encode("utf-8"),
            acks="all",
            retries=5,
            max_request_size=settings.max_message_bytes,
            request_timeout_ms=30000,
            api_version_auto_timeout_ms=10000,
        )
    except NoBrokersAvailable as exc:
        raise ConnectionError(
            f"Kafka is unavailable at {settings.kafka_bootstrap_servers}. Start Docker/Kafka "
            "or check the remote IP and firewall."
        ) from exc


def send_event(
    producer: KafkaProducer,
    topic: str,
    event: dict[str, Any],
    logger: logging.Logger,
) -> bool:
    """Send one event and report failures without terminating a long-running stream."""
    try:
        metadata = producer.send(topic, value=event).get(timeout=30)
        logger.debug(
            "Sent %s partition=%s offset=%s", topic, metadata.partition, metadata.offset
        )
        return True
    except KafkaError as exc:
        logger.error("Kafka send failed for topic %s: %s", topic, exc)
        return False

