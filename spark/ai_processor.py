"""Topic-specific transformations for ai-analysis-results."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType, DoubleType, IntegerType, StringType, StructField, StructType,
)


DETECTION = StructType([
    StructField("class_id", IntegerType()),
    StructField("class_name", StringType()),
    StructField("confidence", DoubleType()),
    StructField("bbox", ArrayType(DoubleType())),
])
SCHEMA = StructType([
    StructField("frame_id", StringType()),
    StructField("source", StringType()),
    StructField("source_topic", StringType()),
    StructField("source_timestamp", DoubleType()),
    StructField("object_key", StringType()),
    StructField("content_hash", StringType()),
    StructField("canonical_image_id", StringType()),
    StructField("status", StringType()),
    StructField("reason", StringType()),
    StructField("model_name", StringType()),
    StructField("detection_count", IntegerType()),
    StructField("max_confidence", DoubleType()),
    StructField("damage_classes", ArrayType(StringType())),
    StructField("detections", ArrayType(DETECTION)),
    StructField("processed_at", DoubleType()),
])


def process(raw: DataFrame) -> DataFrame:
    return (
        raw.select(
            F.from_json("value_text", SCHEMA).alias("event_data"),
            "topic", "partition", "offset", "kafka_timestamp",
        )
        .select("event_data.*", "topic", "partition", "offset", "kafka_timestamp")
        .filter(F.col("frame_id").isNotNull() & F.col("status").isNotNull())
        .withColumn("source_received_at", F.to_timestamp(F.from_unixtime("source_timestamp")))
        .withColumn("ai_processed_at", F.to_timestamp(F.from_unixtime("processed_at")))
        .withColumn("spark_processed_at", F.current_timestamp())
    )
