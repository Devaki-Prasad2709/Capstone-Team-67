"""Environment-backed settings shared by producers, consumers, and Spark."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env")


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        logging.getLogger(__name__).warning("Invalid %s; using %s", name, default)
        return default


def _int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        logging.getLogger(__name__).warning("Invalid %s; using %s", name, default)
        return default


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    kafka_bootstrap_servers: str = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    social_dataset_path: str = os.getenv("SOCIAL_DATASET_PATH", "")
    drone_dataset_path: str = os.getenv("DRONE_DATASET_PATH", "")
    satellite_dataset_path: str = os.getenv("SATELLITE_DATASET_PATH", "")
    gis_dataset_path: str = os.getenv("GIS_DATASET_PATH", "")
    spacenet8_dataset_path: str = os.getenv("SPACENET8_DATASET_PATH", "")
    isbda_dataset_path: str = os.getenv("ISBDA_DATASET_PATH", "")
    final_scenario_path: str = os.getenv(
        "FINAL_SCENARIO_PATH", "scenarios/louisiana_east_flood/scenario.json"
    )
    social_delay: float = _float("SOCIAL_STREAM_DELAY", 1.0)
    drone_delay: float = _float("DRONE_STREAM_DELAY", 0.5)
    satellite_delay: float = _float("SATELLITE_STREAM_DELAY", 5.0)
    gis_delay: float = _float("GIS_STREAM_DELAY", 2.0)
    max_message_bytes: int = _int("KAFKA_MAX_MESSAGE_BYTES", 10 * 1024 * 1024)
    satellite_jpeg_quality: int = _int("SATELLITE_JPEG_QUALITY", 85)
    satellite_change_consumer_group: str = os.getenv(
        "SATELLITE_CHANGE_CONSUMER_GROUP", "satellite-change-v1"
    )
    satellite_change_starting_offsets: str = os.getenv(
        "SATELLITE_CHANGE_STARTING_OFFSETS", "latest"
    )
    satellite_change_grid_size: int = _int("SATELLITE_CHANGE_GRID_SIZE", 8)
    image_transfer_mode: str = os.getenv("IMAGE_TRANSFER_MODE", "base64").lower()
    object_storage_endpoint_url: str = os.getenv(
        "OBJECT_STORAGE_ENDPOINT_URL", "http://localhost:9000"
    )
    object_storage_region: str = os.getenv("OBJECT_STORAGE_REGION", "us-east-1")
    object_storage_bucket: str = os.getenv("OBJECT_STORAGE_BUCKET", "disaster-images")
    object_storage_access_key: str = os.getenv("OBJECT_STORAGE_ACCESS_KEY", "")
    object_storage_secret_key: str = os.getenv("OBJECT_STORAGE_SECRET_KEY", "")
    object_storage_presigned_expiry: int = _int("OBJECT_STORAGE_PRESIGNED_EXPIRY", 3600)
    deduplication_enabled: bool = os.getenv(
        "IMAGE_DEDUPLICATION_ENABLED", "true"
    ).lower() in {"1", "true", "yes", "on"}
    near_duplicate_enabled: bool = os.getenv("NEAR_DUPLICATE_ENABLED", "true").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    near_duplicate_sources: tuple[str, ...] = tuple(
        item.strip().lower()
        for item in os.getenv("NEAR_DUPLICATE_SOURCES", "drone").split(",")
        if item.strip()
    )
    phash_distance_threshold: int = _int("PHASH_DISTANCE_THRESHOLD", 6)
    dedup_lookback_records: int = _int("DEDUP_LOOKBACK_RECORDS", 500)
    dedup_database_path: str = os.getenv(
        "DEDUP_DATABASE_PATH", "storage/dedup/image_fingerprints.sqlite3"
    )
    building_classification_database_path: str = os.getenv(
        "BUILDING_CLASSIFICATION_DATABASE_PATH",
        "storage/gis/building_classifications.sqlite3",
    )
    log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()
    spark_master: str = os.getenv("SPARK_MASTER", "local[*]")
    spark_starting_offsets: str = os.getenv("SPARK_STARTING_OFFSETS", "latest")
    ai_model_path: str = os.getenv(
        "AI_MODEL_PATH",
        "ai/computer_vision/artifacts/drone_detector/weights/best.pt",
    )
    ai_consumer_group: str = os.getenv("AI_CONSUMER_GROUP", "disaster-ai-drone-v1")
    ai_starting_offsets: str = os.getenv("AI_STARTING_OFFSETS", "latest")
    ai_image_size: int = _int("AI_IMAGE_SIZE", 640)
    ai_confidence_threshold: float = _float("AI_CONFIDENCE_THRESHOLD", 0.25)
    ai_device: str = os.getenv("AI_DEVICE", "")
    ai_enable_motion_filter: bool = _bool("AI_ENABLE_MOTION_FILTER", True)
    ai_enable_quality_filter: bool = _bool("AI_ENABLE_QUALITY_FILTER", True)
    # Producer-side SQLite deduplication is persistent, so this legacy bounded
    # second filter is opt-in to avoid suppressing already-unique frames twice.
    ai_enable_near_dedup: bool = _bool("AI_ENABLE_NEAR_DEDUP", False)
    ai_motion_threshold: int = _int("AI_MOTION_THRESHOLD", 5000)
    ai_blur_threshold: float = _float("AI_BLUR_THRESHOLD", 100.0)
    ai_phash_threshold: int = _int("AI_PHASH_THRESHOLD", 5)
    ai_phash_window: int = _int("AI_PHASH_WINDOW", 20)
    ai_allow_local_paths: bool = _bool("AI_ALLOW_LOCAL_PATHS", False)
    disaster_keywords: tuple[str, ...] = tuple(
        word.strip().lower()
        for word in os.getenv(
            "DISASTER_KEYWORDS",
            "flood,fire,earthquake,rescue,injured,damage,storm,hurricane",
        ).split(",")
        if word.strip()
    )

    @property
    def received_drone_dir(self) -> Path:
        return self.project_root / "storage" / "received_images" / "drone"

    @property
    def received_satellite_dir(self) -> Path:
        return self.project_root / "storage" / "received_images" / "satellite"

    @property
    def received_gis_dir(self) -> Path:
        return self.project_root / "storage" / "received_images" / "gis"

    @property
    def resolved_dedup_database_path(self) -> Path:
        path = Path(self.dedup_database_path)
        return path if path.is_absolute() else self.project_root / path

    @property
    def resolved_ai_model_path(self) -> Path:
        path = Path(self.ai_model_path)
        return path if path.is_absolute() else self.project_root / path

    @property
    def resolved_building_classification_database_path(self) -> Path:
        path = Path(self.building_classification_database_path)
        return path if path.is_absolute() else self.project_root / path


settings = Settings()


def configure_logging(component: str) -> logging.Logger:
    """Configure console and rotating-by-run file logging."""
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_dir / f"{component}.log", encoding="utf-8"),
        ],
        force=True,
    )
    return logging.getLogger(component)


def require_directory(raw_path: str, setting_name: str) -> Path:
    """Return a configured dataset directory or raise a beginner-friendly error."""
    if not raw_path.strip():
        raise FileNotFoundError(f"{setting_name} is empty. Set it in .env.")
    path = Path(raw_path).expanduser()
    if not path.is_dir():
        raise FileNotFoundError(
            f"Dataset directory does not exist: {path}. Check {setting_name} in .env "
            "and confirm the removable drive is connected."
        )
    return path
