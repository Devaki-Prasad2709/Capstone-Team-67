"""Portable YOLO26s -> event contract -> GIS observation integration test."""

from __future__ import annotations

import os
import time
import unittest
from pathlib import Path

import cv2

from ai.computer_vision.contracts import result_event
from ai.computer_vision.detector import DamageDetector
from config.settings import settings
from core.gis.building_association import SpatialIndex, build_building_lookup
from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.observation.geolocator import ManualOverrideGeoLocator
from core.observation.ingest import ingest_ai_analysis_result
from core.observation.observation_log import ObservationLog
from core.observation.state_update import apply_observations


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "core" / "gis" / "fixtures" / "demo_gis.geojson"
MODEL = ROOT / "ai" / "computer_vision" / "artifacts" / "drone_detector" / "weights" / "best.pt"
BUNDLED_IMAGE = (
    ROOT
    / "ai"
    / "computer_vision"
    / "artifacts"
    / "drone_detector"
    / "val_batch0_labels.jpg"
)


def integration_image() -> Path:
    """Use an explicit image, configured drone data, or the tracked fixture."""
    explicit = os.getenv("YOLO26S_TEST_IMAGE", "").strip()
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"YOLO26S_TEST_IMAGE does not exist: {path}")
        return path

    configured = Path(settings.drone_dataset_path).expanduser()
    if settings.drone_dataset_path and configured.is_dir():
        images = sorted(
            path
            for path in configured.rglob("*")
            if path.suffix.lower() in {".jpg", ".jpeg", ".png"}
        )
        if images:
            return images[0]

    if not BUNDLED_IMAGE.is_file():
        raise FileNotFoundError(f"Bundled YOLO integration fixture is missing: {BUNDLED_IMAGE}")
    return BUNDLED_IMAGE


class YOLO26sIngestTests(unittest.TestCase):
    def test_real_checkpoint_updates_gis_graph(self) -> None:
        gis_data = load_gis(str(FIXTURE))
        initial, id_map = build_graph_from_gis(gis_data, seed=42)
        spatial_index = SpatialIndex(
            gis_data,
            build_building_lookup(gis_data, id_map),
            id_map,
        )

        image_path = integration_image()
        image = cv2.imread(str(image_path))
        self.assertIsNotNone(image, f"Could not decode integration image: {image_path}")

        detector = DamageDetector(MODEL, device=settings.ai_device or "cpu")
        self.assertEqual(
            detector.checkpoint_sha256,
            "780241f6b42f9f0b0d83a8be8d3168e1ca864a756ff93907e72bbb7f9fe3cbf9",
        )
        detections = detector.predict([image])[0]
        self.assertGreater(len(detections), 0, f"No detections for integration image: {image_path}")
        self.assertTrue(
            all(item["class_name"] in {"Slight", "Severe", "Debris"} for item in detections)
        )

        event = result_event(
            {
                "frame_id": "yolo26s-integration-001",
                "source": "drone",
                "timestamp": time.time(),
                "object_key": str(image_path),
                "content_hash": "integration-test",
            },
            "analyzed",
            detections=detections,
        )
        observation_log = ObservationLog()
        result = ingest_ai_analysis_result(
            event,
            ManualOverrideGeoLocator(),
            spatial_index,
            observation_log,
            drone_telemetry={"override_lat": 12.7106, "override_lon": 77.6949},
        )
        updated = apply_observations(initial, observation_log, now=time.time())

        self.assertEqual(event["model_name"], "drone_detector_yolo26s")
        self.assertEqual(result.detections_seen, len(detections))
        self.assertEqual(result.detections_ingested, len(detections))
        self.assertEqual(result.detections_skipped_no_node, 0)
        self.assertTrue(result.touched_node_ids)
        for node_id in result.touched_node_ids:
            self.assertGreater(updated.nodes[node_id]["damage"], 0.0)


if __name__ == "__main__":
    unittest.main()
