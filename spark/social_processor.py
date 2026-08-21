"""Topic-specific transformations for social-posts."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import DoubleType, StringType, StructField, StructType


SCHEMA = StructType(
    [
        StructField("id", StringType()),
        StructField("text", StringType()),
        StructField("hazard", StringType()),
        StructField("event", StringType()),
        StructField("timestamp", DoubleType()),
        StructField("source", StringType()),
        StructField("data_type", StringType()),
    ]
)


def process(raw: DataFrame, keywords: tuple[str, ...]) -> DataFrame:
    """Parse, validate, normalize, and enrich social records."""
    parsed = raw.select(
        F.from_json(F.col("value_text"), SCHEMA).alias("event_data"),
        "topic",
        "partition",
        "offset",
        "kafka_timestamp",
    ).select("event_data.*", "topic", "partition", "offset", "kafka_timestamp")
    normalized = parsed.filter(
        F.col("id").isNotNull() & F.col("text").isNotNull() & (F.length("text") > 0)
    ).withColumn("normalized_text", F.trim(F.regexp_replace(F.lower("text"), r"\s+", " ")))
    keyword_pattern = "|".join(f"(?:{word})" for word in keywords) if keywords else r"a^"
    return (
        normalized.withColumn("keyword_match", F.col("normalized_text").rlike(keyword_pattern))
        .withColumn("event_timestamp", F.to_timestamp(F.from_unixtime("timestamp")))
        .withColumn("processed_at", F.current_timestamp())
        .select(
            "id",
            "text",
            "normalized_text",
            "hazard",
            "event",
            "source",
            "keyword_match",
            "event_timestamp",
            "processed_at",
            "topic",
            "partition",
            "offset",
            "kafka_timestamp",
        )
    )


def analytics(processed: DataFrame) -> DataFrame:
    """Demonstration counts only; this is not an AI classifier."""
    return processed.groupBy("event", "hazard").count()

