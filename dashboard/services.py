"""Read-only operational metrics shared by the web dashboard."""

from __future__ import annotations

import json
import sqlite3
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any

import boto3
import pandas as pd
from botocore.client import Config
from kafka import KafkaConsumer, TopicPartition

from config.settings import settings


PROJECT_ROOT = Path(__file__).resolve().parents[1]
from common.topics import ALL_TOPICS

TOPICS = ALL_TOPICS


def run_command(command: list[str], timeout: int = 45) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, str(exc)
    output = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    return result.returncode == 0, output or "Command completed."


def docker_services() -> tuple[list[dict[str, Any]], str | None]:
    ok, output = run_command(["docker", "compose", "ps", "--format", "json"], 12)
    if not ok:
        return [], output
    try:
        decoded = json.loads(output)
        return (decoded if isinstance(decoded, list) else [decoded]), None
    except json.JSONDecodeError:
        try:
            return [json.loads(line) for line in output.splitlines() if line.strip()], None
        except json.JSONDecodeError:
            return [], output


def kafka_counts() -> tuple[dict[str, int | None], str | None]:
    consumer = None
    try:
        consumer = KafkaConsumer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=None,
            enable_auto_commit=False,
            request_timeout_ms=4000,
            api_version_auto_timeout_ms=3000,
        )
        counts: dict[str, int | None] = {}
        for topic in TOPICS:
            partitions = consumer.partitions_for_topic(topic)
            if not partitions:
                counts[topic] = None
                continue
            items = [TopicPartition(topic, number) for number in partitions]
            beginning = consumer.beginning_offsets(items)
            end = consumer.end_offsets(items)
            counts[topic] = sum(end[item] - beginning[item] for item in items)
        return counts, None
    except Exception as exc:
        return {}, str(exc)
    finally:
        if consumer is not None:
            consumer.close()


def recent_topic_events(topic: str, limit: int = 50) -> tuple[list[dict[str, Any]], str | None]:
    """Read a bounded tail without joining or advancing a consumer group."""
    consumer = None
    try:
        consumer = KafkaConsumer(
            bootstrap_servers=settings.kafka_bootstrap_servers,
            group_id=None,
            enable_auto_commit=False,
            consumer_timeout_ms=1500,
            request_timeout_ms=4000,
            api_version_auto_timeout_ms=3000,
        )
        partitions = consumer.partitions_for_topic(topic)
        if not partitions:
            return [], None
        items = [TopicPartition(topic, number) for number in sorted(partitions)]
        consumer.assign(items)
        beginnings = consumer.beginning_offsets(items)
        ends = consumer.end_offsets(items)
        per_partition = max(1, limit // len(items) + 1)
        for item in items:
            consumer.seek(item, max(beginnings[item], ends[item] - per_partition))
        rows: list[dict[str, Any]] = []
        for message in consumer:
            try:
                event = json.loads(message.value.decode("utf-8"))
                if isinstance(event, dict):
                    event["_kafka_partition"] = message.partition
                    event["_kafka_offset"] = message.offset
                    rows.append(event)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        rows.sort(key=lambda item: float(item.get("processed_at") or 0), reverse=True)
        return rows[:limit], None
    except Exception as exc:
        return [], str(exc)
    finally:
        if consumer is not None:
            consumer.close()


def ai_model_summary() -> tuple[dict[str, Any], str | None]:
    manifest = PROJECT_ROOT / "ai" / "computer_vision" / "artifact_manifest.json"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        data["model_available"] = settings.resolved_ai_model_path.is_file()
        data["model_path"] = str(settings.resolved_ai_model_path.relative_to(PROJECT_ROOT))
        return data, None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {}, str(exc)


def object_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.object_storage_endpoint_url or None,
        region_name=settings.object_storage_region,
        aws_access_key_id=settings.object_storage_access_key,
        aws_secret_access_key=settings.object_storage_secret_key,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def minio_objects(limit: int = 30) -> tuple[dict[str, dict[str, int]], list[dict[str, Any]], str | None]:
    try:
        client = object_client()
        summary: dict[str, dict[str, int]] = {}
        recent: list[dict[str, Any]] = []
        for source in ("drone", "satellite", "gis"):
            objects: list[dict[str, Any]] = []
            paginator = client.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=settings.object_storage_bucket, Prefix=f"{source}/"):
                objects.extend(page.get("Contents", []))
            summary[source] = {
                "objects": len(objects),
                "bytes": sum(int(item.get("Size", 0)) for item in objects),
            }
            recent.extend(
                {
                    "source": source,
                    "key": str(item["Key"]),
                    "size_bytes": int(item.get("Size", 0)),
                    "last_modified": serialize(item.get("LastModified")),
                }
                for item in objects
            )
        recent.sort(key=lambda item: str(item["last_modified"]), reverse=True)
        return summary, recent[:limit], None
    except Exception as exc:
        return {}, [], str(exc)


def dedup_summary() -> tuple[dict[str, int], str | None]:
    database = settings.resolved_dedup_database_path
    if not database.exists():
        return {}, None
    try:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            rows = connection.execute(
                "SELECT source, COUNT(*) FROM image_fingerprints GROUP BY source"
            ).fetchall()
        return {str(source): int(total) for source, total in rows}, None
    except sqlite3.Error as exc:
        return {}, str(exc)


def parquet_preview(source: str, limit: int = 100) -> tuple[list[dict[str, Any]], int, str | None]:
    folder = PROJECT_ROOT / "storage" / "processed" / source
    files = sorted(folder.glob("*.parquet"), key=lambda item: item.stat().st_mtime, reverse=True)
    if not files:
        return [], 0, None
    try:
        frames = [pd.read_parquet(file) for file in files[:8]]
        frame = pd.concat(frames, ignore_index=True).tail(limit)
        frame = frame.where(pd.notnull(frame), None)
        return [
            {str(key): serialize(value) for key, value in row.items()}
            for row in frame.to_dict(orient="records")
        ], len(files), None
    except Exception as exc:
        return [], len(files), str(exc)


def serialize(value: Any) -> Any:
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): serialize(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [serialize(item) for item in value]
    if hasattr(value, "tolist"):
        return serialize(value.tolist())
    if hasattr(value, "item"):
        return value.item()
    return value
