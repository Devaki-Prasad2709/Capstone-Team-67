"""Canonical Kafka topic names shared by setup, processing, and monitoring."""

SOCIAL_TOPIC = "social-posts"
DRONE_TOPIC = "drone-video"
SATELLITE_TOPIC = "satellite-imagery"
GIS_TOPIC = "gis-data"
AI_RESULTS_TOPIC = "ai-analysis-results"
TELEMETRY_TOPIC = "infrastructure-telemetry"
SATELLITE_CHANGE_TOPIC = "satellite-change-results"

ALL_TOPICS = (
    SOCIAL_TOPIC,
    DRONE_TOPIC,
    SATELLITE_TOPIC,
    GIS_TOPIC,
    AI_RESULTS_TOPIC,
    TELEMETRY_TOPIC,
    SATELLITE_CHANGE_TOPIC,
)
