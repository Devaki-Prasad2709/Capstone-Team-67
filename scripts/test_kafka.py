"""Check broker connectivity and required topics without modifying data."""

from __future__ import annotations

from kafka import KafkaConsumer
from kafka.errors import KafkaError

from config.settings import settings
from scripts.create_topics import TOPICS


def main() -> None:
    print(f"Testing Kafka at {settings.kafka_bootstrap_servers}...")
    try:
        consumer = KafkaConsumer(
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            request_timeout_ms=10000,
            api_version_auto_timeout_ms=10000,
        )
        available = consumer.topics()
        consumer.close()
    except KafkaError as exc:
        raise SystemExit(f"FAILED: cannot connect to Kafka: {exc}") from exc
    missing = sorted(set(TOPICS) - available)
    if missing:
        raise SystemExit(f"CONNECTED, but topics are missing: {', '.join(missing)}")
    print("SUCCESS: broker is reachable and all required topics exist.")


if __name__ == "__main__":
    main()

