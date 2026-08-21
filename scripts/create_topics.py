"""Create all project Kafka topics idempotently."""

from __future__ import annotations

from kafka.admin import KafkaAdminClient, NewTopic
from kafka.errors import NoBrokersAvailable, TopicAlreadyExistsError

from config.settings import configure_logging, settings


from common.topics import ALL_TOPICS


TOPICS = list(ALL_TOPICS)
logger = configure_logging("create_topics")


def main() -> None:
    try:
        admin = KafkaAdminClient(
            bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
            client_id="disaster-topic-initializer",
        )
    except NoBrokersAvailable as exc:
        logger.error("Kafka is unavailable at %s", settings.kafka_bootstrap_servers)
        raise SystemExit(1) from exc
    try:
        existing = set(admin.list_topics())
        missing = [
            NewTopic(name=name, num_partitions=3, replication_factor=1)
            for name in TOPICS
            if name not in existing
        ]
        if missing:
            try:
                admin.create_topics(new_topics=missing, validate_only=False)
            except TopicAlreadyExistsError:
                pass
        final_topics = set(admin.list_topics())
        for name in TOPICS:
            logger.info("%s: %s", name, "ready" if name in final_topics else "missing")
    finally:
        admin.close()


if __name__ == "__main__":
    main()
