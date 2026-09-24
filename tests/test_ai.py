from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from ai.computer_vision.contracts import result_event
from ai.computer_vision.worker import image_bytes
from ai.preprocessing.pipeline import BatchManager, PerceptualDeduplicator, PreprocessingPipeline
from ai.computer_vision.tools.convert_coco_to_yolo import convert
from scripts.verify_ai_artifacts import verify
from dashboard.services import serialize


def encoded_test_image() -> bytes:
    image = np.zeros((96, 128, 3), dtype=np.uint8)
    cv2.rectangle(image, (8, 8), (110, 80), (255, 255, 255), 3)
    cv2.line(image, (0, 95), (127, 0), (0, 220, 255), 2)
    ok, buffer = cv2.imencode(".jpg", image)
    if not ok:
        raise RuntimeError("Could not make test image")
    return buffer.tobytes()


class PreprocessingTests(unittest.TestCase):
    def test_valid_image_is_resized(self) -> None:
        result = PreprocessingPipeline(
            image_size=64, enable_motion=False, enable_quality=False
        ).process_bytes(encoded_test_image())
        self.assertTrue(result.accepted)
        self.assertEqual(result.image.shape, (64, 64, 3))
        self.assertEqual(result.metadata["original_width"], 128)

    def test_invalid_bytes_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            PreprocessingPipeline().process_bytes(b"not an image")

    def test_bounded_phash_detects_same_visual(self) -> None:
        image = PreprocessingPipeline.decode(encoded_test_image())
        detector = PerceptualDeduplicator(window_size=20, threshold=5)
        self.assertFalse(detector.is_duplicate(image)[0])
        self.assertTrue(detector.is_duplicate(image.copy())[0])

    def test_batch_manager_flushes_full_and_partial_batches(self) -> None:
        manager = BatchManager(2)
        self.assertIsNone(manager.add("a"))
        self.assertEqual(manager.add("b"), ["a", "b"])
        self.assertIsNone(manager.add("c"))
        self.assertEqual(manager.flush(), ["c"])


class DatasetToolTests(unittest.TestCase):
    def test_damage_bbox_is_converted_to_yolo(self) -> None:
        coco = {
            "images": [{"id": 1, "file_name": "frame.jpg", "width": 100, "height": 50}],
            "annotations": [{"image_id": 1, "category_id": 2, "damage_bbox": [10, 5, 20, 10]}],
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, output = root / "instances.json", root / "labels"
            source.write_text(json.dumps(coco), encoding="utf-8")
            self.assertEqual(convert(source, output), 1)
            self.assertEqual((output / "frame.txt").read_text(encoding="utf-8"), "1 0.200000 0.200000 0.200000 0.200000\n")


class AIContractTests(unittest.TestCase):
    def test_base64_event_and_result_contract(self) -> None:
        payload = encoded_test_image()
        event = {
            "frame_id": "frame.jpg",
            "source": "drone",
            "timestamp": 123.0,
            "transfer_mode": "base64",
            "image_data": base64.b64encode(payload).decode("ascii"),
        }
        self.assertEqual(image_bytes(event), payload)
        output = result_event(
            event,
            "analyzed",
            [{"class_id": 1, "class_name": "damage_class_2", "confidence": 0.8, "bbox": [1, 2, 3, 4]}],
        )
        self.assertEqual(output["detection_count"], 1)
        self.assertEqual(output["damage_classes"], ["damage_class_2"])
        self.assertEqual(output["max_confidence"], 0.8)


class ArtifactIntegrityTests(unittest.TestCase):
    def test_preserved_model_and_metrics(self) -> None:
        self.assertEqual(
            verify(),
            {"precision": 0.36917, "recall": 0.28202, "map50": 0.25171, "map50_95": 0.10874},
        )


class DashboardSerializationTests(unittest.TestCase):
    def test_nested_spark_detection_arrays_are_json_ready(self) -> None:
        value = np.array(
            [{"class_id": 0, "bbox": np.array([1.0, 2.0, 3.0, 4.0])}],
            dtype=object,
        )
        self.assertEqual(
            serialize(value),
            [{"class_id": 0, "bbox": [1.0, 2.0, 3.0, 4.0]}],
        )


if __name__ == "__main__":
    unittest.main()
