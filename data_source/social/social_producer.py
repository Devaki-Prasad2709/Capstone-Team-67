"""Stream CrisisMMD TSV rows to the social-posts Kafka topic."""

from __future__ import annotations

import argparse
import csv
import time
from pathlib import Path
from typing import Iterator

from common.kafka_utils import create_json_producer, send_event
from config.settings import configure_logging, require_directory, settings


TOPIC = "social-posts"
logger = configure_logging("social_producer")


def discover_tsv_files(dataset_dir: Path) -> list[Path]:
    return sorted(path for path in dataset_dir.rglob("*.tsv") if path.is_file())


def standardize_record(row: dict[str, str]) -> dict[str, object] | None:
    """Map a CrisisMMD row to the stable social event schema."""
    tweet_id = (row.get("tweet_id") or row.get("id") or "").strip()
    text = (row.get("tweet_text") or row.get("text") or "").strip()
    if not tweet_id or not text:
        return None
    hazard = (
        row.get("label")
        or row.get("label_text")
        or row.get("informative")
        or "unknown"
    ).strip()
    return {
        "id": tweet_id,
        "text": text,
        "hazard": hazard or "unknown",
        "event": (row.get("event_name") or row.get("event") or "unknown").strip(),
        "timestamp": time.time(),
        "source": "twitter",
        "data_type": "text",
    }


def iter_events(files: list[Path]) -> Iterator[dict[str, object]]:
    for file_path in files:
        logger.info("Reading %s", file_path)
        try:
            with file_path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
                for line_number, row in enumerate(csv.DictReader(handle, delimiter="\t"), start=2):
                    event = standardize_record(row)
                    if event is None:
                        logger.warning("Skipping invalid row %s:%d", file_path.name, line_number)
                        continue
                    yield event
        except (OSError, csv.Error) as exc:
            logger.error("Could not read %s: %s", file_path, exc)


def run(dataset_path: str, delay: float, limit: int | None = None) -> int:
    dataset_dir = require_directory(dataset_path, "SOCIAL_DATASET_PATH")
    files = discover_tsv_files(dataset_dir)
    if not files:
        raise FileNotFoundError(f"No .tsv files found under {dataset_dir}")
    producer = create_json_producer()
    sent = 0
    try:
        for event in iter_events(files):
            if send_event(producer, TOPIC, event, logger):
                sent += 1
                logger.info("Sent social post id=%s event=%s", event["id"], event["event"])
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
    parser.add_argument("--dataset", default=settings.social_dataset_path)
    parser.add_argument("--delay", type=float, default=settings.social_delay)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    try:
        logger.info("Completed after sending %d records", run(args.dataset, args.delay, args.limit))
    except (FileNotFoundError, ConnectionError) as exc:
        logger.error("%s", exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()

