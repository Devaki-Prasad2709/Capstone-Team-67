"""Validate GIS image-reference events."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import BooleanType, DoubleType, LongType, StringType, StructField, StructType


SCHEMA = StructType([
    StructField("map_id", StringType()), StructField("timestamp", DoubleType()),
    StructField("source", StringType()), StructField("format", StringType()),
    StructField("transfer_mode", StringType()), StructField("image_uri", StringType()),
    StructField("object_key", StringType()), StructField("content_hash", StringType()),
    StructField("size_bytes", LongType()), StructField("is_duplicate", BooleanType()),
    StructField("canonical_image_id", StringType()),
])


def process(raw: DataFrame) -> DataFrame:
    return (
        raw.select(F.from_json("value_text", SCHEMA).alias("event_data"), "topic", "partition", "offset", "kafka_timestamp")
        .select("event_data.*", "topic", "partition", "offset", "kafka_timestamp")
        .filter(F.col("map_id").isNotNull() & (F.col("source") == "gis"))
        .withColumn("received_at", F.to_timestamp(F.from_unixtime("timestamp")))
        .withColumn("processed_at", F.current_timestamp())
        .withColumn("processing_status", F.when(F.col("is_duplicate"), "duplicate_skipped").otherwise("ready"))
    )
