from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

import cv2
import numpy as np

from common.deduplication import ImageDeduplicator, content_hash, hamming_distance
from common.image_transfer import ImageTransfer
from common.object_storage import StoredObject
from consumer.interactive_consumer import parse_topic_choice, save_base64_image
from data_source.drone.drone_producer import build_event as build_drone_event
from data_source.social.social_producer import standardize_record


class TopicChoiceTests(unittest.TestCase):
    def test_all(self) -> None:
        self.assertEqual(
            parse_topic_choice("4"),
            ["social-posts", "drone-video", "satellite-imagery", "ai-analysis-results", "gis-data"],
        )

    def test_combination_is_deduplicated(self) -> None:
        self.assertEqual(parse_topic_choice("1,2,1"), ["social-posts", "drone-video"])

    def test_invalid(self) -> None:
        with self.assertRaises(ValueError):
            parse_topic_choice("2,4")


class SocialTransformTests(unittest.TestCase):
    def test_standard_record(self) -> None:
        event = standardize_record(
            {
                "tweet_id": "123",
                "tweet_text": "Need rescue",
                "label": "rescue",
                "event_name": "hurricane_harvey",
            }
        )
        self.assertIsNotNone(event)
        self.assertEqual(event["id"], "123")
        self.assertEqual(event["source"], "twitter")

    def test_missing_text_is_rejected(self) -> None:
        self.assertIsNone(standardize_record({"tweet_id": "123", "tweet_text": ""}))


class ImageDecodeTests(unittest.TestCase):
    def test_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "frame.jpg"
            save_base64_image(base64.b64encode(b"jpeg-bytes").decode("ascii"), destination)
            self.assertEqual(destination.read_bytes(), b"jpeg-bytes")

    def test_bad_base64(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                save_base64_image("not base64!", Path(directory) / "bad.jpg")


class DeduplicationTests(unittest.TestCase):
    def test_exact_duplicate_points_to_canonical(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "dedup.sqlite3"
            first_path = root / "frame-1.jpg"
            second_path = root / "frame-2.jpg"
            image = np.full((32, 48, 3), 100, dtype=np.uint8)
            self.assertTrue(cv2.imwrite(str(first_path), image))
            second_path.write_bytes(first_path.read_bytes())

            deduplicator = ImageDeduplicator(database)
            # Keep this unit test independent of the user's configured MinIO mode.
            transfer = ImageTransfer.__new__(ImageTransfer)
            transfer.mode = "base64"
            transfer.storage = None
            first = build_drone_event(first_path, deduplicator, transfer)
            second = build_drone_event(second_path, deduplicator, transfer)

            self.assertFalse(first["is_duplicate"])
            self.assertTrue(second["is_duplicate"])
            self.assertEqual(second["canonical_image_id"], "frame-1.jpg")
            self.assertEqual(second["canonical_content_hash"], first["content_hash"])
            self.assertEqual(second["duplicate_method"], "sha256")
            self.assertNotIn("image_data", second)

    def test_hash_helpers(self) -> None:
        self.assertEqual(content_hash(b"same"), content_hash(b"same"))
        self.assertEqual(hamming_distance("0000000000000000", "0000000000000001"), 1)

    def test_near_duplicate_applies_to_drone(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            deduplicator = ImageDeduplicator(Path(directory) / "dedup.sqlite3")
            image = np.full((32, 48, 3), 120, dtype=np.uint8)
            first = deduplicator.check_and_register("drone", "one.jpg", b"version-1", image)
            second = deduplicator.check_and_register("drone", "two.jpg", b"version-2", image)
            self.assertFalse(first.is_duplicate)
            self.assertTrue(second.is_duplicate)
            self.assertEqual(second.duplicate_method, "phash")


class ImageTransferTests(unittest.TestCase):
    def test_object_mode_sends_reference_not_payload(self) -> None:
        class FakeStorage:
            def upload(self, key: str, data: bytes, content_type: str) -> StoredObject:
                return StoredObject("bucket", key, f"s3://bucket/{key}", "https://example.test/x")

        transfer = ImageTransfer.__new__(ImageTransfer)
        transfer.mode = "object_storage"
        transfer.storage = FakeStorage()
        event = transfer.attach({}, b"image", "drone", "frame.jpg", "a" * 64)
        self.assertEqual(event["transfer_mode"], "object_storage")
        self.assertIn("image_uri", event)
        self.assertNotIn("image_data", event)


if __name__ == "__main__":
    unittest.main()
