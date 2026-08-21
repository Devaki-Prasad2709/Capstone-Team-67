"""Topic-specific transformations for drone-video."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import (
    BooleanType,
    DoubleType,
    IntegerType,
    LongType,
    StringType,
    StructField,
    StructType,
)


SCHEMA = StructType(
    [
        StructField("frame_id", StringType()),
        StructField("image_data", StringType()),
        StructField("timestamp", DoubleType()),
        StructField("source", StringType()),
        StructField("data_type", StringType()),
        StructField("format", StringType()),
        StructField("transfer_mode", StringType()),
        StructField("image_uri", StringType()),
        StructField("object_key", StringType()),
        StructField("content_hash", StringType()),
        StructField("size_bytes", LongType()),
        StructField("is_duplicate", BooleanType()),
        StructField("canonical_image_id", StringType()),
        StructField("canonical_content_hash", StringType()),
        StructField("duplicate_method", StringType()),
        StructField("perceptual_distance", IntegerType()),
    ]
)


def process(raw: DataFrame) -> DataFrame:
    """Validate frame metadata and deliberately discard the Base64 column."""
    return (
        raw.select(
            F.from_json("value_text", SCHEMA).alias("event_data"),
            "topic",
            "partition",
            "offset",
            "kafka_timestamp",
        )
        .select("event_data.*", "topic", "partition", "offset", "kafka_timestamp")
        .filter(
            F.col("frame_id").isNotNull()
            & (F.col("source") == "drone")
            & F.lower("format").isin("jpg", "jpeg")
            & (
                F.coalesce(F.col("is_duplicate"), F.lit(False))
                | F.col("image_data").isNotNull()
                | F.col("image_uri").isNotNull()
            )
        )
        .withColumn("received_at", F.to_timestamp(F.from_unixtime("timestamp")))
        .withColumn("processed_at", F.current_timestamp())
        .withColumn(
            "processing_status",
            F.when(F.col("is_duplicate") == F.lit(True), F.lit("duplicate_skipped")).otherwise(
                F.lit("ready_for_ai")
            ),
        )
        .select(
            "frame_id",
            "source",
            "format",
            "transfer_mode",
            "image_uri",
            "object_key",
            "content_hash",
            "size_bytes",
            "is_duplicate",
            "canonical_image_id",
            "canonical_content_hash",
            "duplicate_method",
            "perceptual_distance",
            "received_at",
            "processed_at",
            "processing_status",
            "topic",
            "partition",
            "offset",
            "kafka_timestamp",
        )
    )
