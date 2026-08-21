"""Run the complete topic-segregated Spark Structured Streaming layer."""

from __future__ import annotations

import sys
from pathlib import Path

# spark-submit adds spark/ rather than the project root to sys.path.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pyspark.sql import DataFrame, SparkSession  # noqa: E402
from pyspark.sql.streaming import StreamingQuery  # noqa: E402

from config.settings import configure_logging, settings  # noqa: E402
from spark import ai_processor, drone_processor, gis_processor, satellite_processor, social_processor  # noqa: E402


logger = configure_logging("spark_streaming")


def build_spark() -> SparkSession:
    return (
        SparkSession.builder.appName("DisasterStructuredStreaming")
        .master(settings.spark_master)
        .config("spark.sql.shuffle.partitions", "4")
        .getOrCreate()
    )


def read_kafka(spark: SparkSession) -> DataFrame:
    return (
        spark.readStream.format("kafka")
        .option("kafka.bootstrap.servers", settings.kafka_bootstrap_servers)
        .option("subscribe", "social-posts,drone-video,satellite-imagery,gis-data,ai-analysis-results")
        .option("startingOffsets", settings.spark_starting_offsets)
        .option("failOnDataLoss", "false")
        .option("kafka.max.partition.fetch.bytes", str(settings.max_message_bytes))
        .load()
        .selectExpr(
            "CAST(value AS STRING) AS value_text",
            "topic",
            "partition",
            "offset",
            "timestamp AS kafka_timestamp",
        )
    )


def parquet_query(frame: DataFrame, name: str) -> StreamingQuery:
    output = PROJECT_ROOT / "storage" / "processed" / name
    checkpoint = PROJECT_ROOT / "storage" / "checkpoints" / name
    return (
        frame.writeStream.format("parquet")
        .outputMode("append")
        .option("path", str(output))
        .option("checkpointLocation", str(checkpoint))
        .queryName(f"{name}_parquet")
        .start()
    )


def console_query(frame: DataFrame, name: str, mode: str = "append") -> StreamingQuery:
    return (
        frame.writeStream.format("console")
        .outputMode(mode)
        .option("truncate", "false")
        .option("numRows", "30")
        .queryName(name)
        .start()
    )


def main() -> None:
    spark = build_spark()
    spark.sparkContext.setLogLevel("WARN")
    raw = read_kafka(spark)

    social_raw = raw.filter("topic = 'social-posts'")
    drone_raw = raw.filter("topic = 'drone-video'")
    satellite_raw = raw.filter("topic = 'satellite-imagery'")
    ai_raw = raw.filter("topic = 'ai-analysis-results'")
    gis_raw = raw.filter("topic = 'gis-data'")

    social = social_processor.process(social_raw, settings.disaster_keywords)
    drone = drone_processor.process(drone_raw)
    satellite = satellite_processor.process(satellite_raw)
    ai = ai_processor.process(ai_raw)
    gis = gis_processor.process(gis_raw)

    queries = [
        parquet_query(social, "social"),
        parquet_query(drone, "drone"),
        parquet_query(satellite, "satellite"),
        parquet_query(ai, "ai"),
        parquet_query(gis, "gis"),
        console_query(social, "SOCIAL"),
        console_query(drone, "DRONE"),
        console_query(satellite, "SATELLITE"),
        console_query(ai, "AI_RESULTS"),
        console_query(gis, "GIS"),
        console_query(social_processor.analytics(social), "SOCIAL_COUNTS", "complete"),
    ]
    logger.info("Spark queries started. Press Ctrl+C to stop.")
    try:
        spark.streams.awaitAnyTermination()
    except KeyboardInterrupt:
        logger.info("Stopping Spark queries")
    finally:
        for query in queries:
            query.stop()
        spark.stop()


if __name__ == "__main__":
    main()
