"""Topic-specific transformations for ai-analysis-results."""

from __future__ import annotations

from pyspark.sql import DataFrame
from pyspark.sql import functions as F
from pyspark.sql.types import (
    ArrayType, BooleanType, DoubleType, IntegerType, StringType, StructField, StructType,
)


GPS = StructType([
    StructField("longitude", DoubleType()),
    StructField("latitude", DoubleType()),
])
IMAGE_REFERENCE = StructType([
    StructField("frame_id", StringType()),
    StructField("asset_id", StringType()),
    StructField("transfer_mode", StringType()),
    StructField("object_key", StringType()),
    StructField("image_uri", StringType()),
    StructField("content_hash", StringType()),
])
TARGET_ASSOCIATION = StructType([
    StructField("declared_target_id", StringType()),
    StructField("provenance", StringType()),
])
DETECTION = StructType([
    StructField("class_id", IntegerType()),
    StructField("class_name", StringType()),
    StructField("confidence", DoubleType()),
    StructField("bbox", ArrayType(DoubleType())),
    StructField("image_reference", IMAGE_REFERENCE),
    StructField("scenario_timestamp", StringType()),
    StructField("gps", GPS),
    StructField("gps_provenance", StringType()),
    StructField("target_association", TARGET_ASSOCIATION),
])
SCHEMA = StructType([
    StructField("frame_id", StringType()),
    StructField("source", StringType()),
    StructField("source_topic", StringType()),
    StructField("source_timestamp", DoubleType()),
    StructField("object_key", StringType()),
    StructField("image_uri", StringType()),
    StructField("transfer_mode", StringType()),
    StructField("content_hash", StringType()),
    StructField("canonical_image_id", StringType()),
    StructField("status", StringType()),
    StructField("reason", StringType()),
    StructField("model_name", StringType()),
    StructField("model_checkpoint_sha256", StringType()),
    StructField("inference_provenance", StringType()),
    StructField("prerecorded_output", BooleanType()),
    StructField("scenario_id", StringType()),
    StructField("scenario_event_id", StringType()),
    StructField("scenario_timestamp", StringType()),
    StructField("gps", GPS),
    StructField("gps_provenance", StringType()),
    StructField("target_id", StringType()),
    StructField("target_association_provenance", StringType()),
    StructField("input_origin", StringType()),
    StructField("simulation_fields", ArrayType(StringType())),
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
