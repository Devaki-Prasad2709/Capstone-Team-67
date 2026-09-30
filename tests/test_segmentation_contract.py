"""Regression checks for segmentation geometry and legacy observations."""

import unittest

from ai.computer_vision.contracts import result_event
from core.observation.ingest import detection_from_ai_result_entry
from core.observation.observation_log import ObservationLog, ObservationRecord


class SegmentationContractTests(unittest.TestCase):
    def record(self, **overrides):
        values = dict(
            observation_id="obs", timestamp=100.0, frame_id="frame", track_id=None,
            source="drone", class_id=4, class_name="building_total_destruction",
            confidence=0.8, bbox=(0, 0, 20, 20), latitude=12.7, longitude=77.6,
            working_x=0.0, working_y=0.0, building_id=None, node_id=1,
        )
        values.update(overrides)
        return ObservationRecord(**values)

    def test_legacy_caller_can_omit_mask(self):
        self.assertIsNone(self.record().mask)

    def test_mask_survives_event_and_detection_adapter(self):
        polygon = [[1.0, 2.0], [20.0, 2.0], [20.0, 15.0]]
        entry = dict(class_id=4, class_name="building_total_destruction",
                     confidence=0.8, bbox=[0, 0, 20, 20], mask=polygon)
        event = result_event({"frame_id": "frame", "timestamp": 100.0},
                             "analyzed", detections=[entry])
        detection = detection_from_ai_result_entry(event, event["detections"][0])
        self.assertEqual(detection.mask, polygon)
        self.assertEqual(detection.class_name, "building_total_destruction")

    def test_context_does_not_create_or_dilute_damage(self):
        log = ObservationLog()
        log.add(self.record(class_id=8, class_name="tree"))
        self.assertEqual(log.aggregate_damage(1, now=100.0), 0.0)
        log.add(self.record(observation_id="damage"))
        self.assertAlmostEqual(log.aggregate_damage(1, now=100.0), 0.8)


if __name__ == "__main__":
    unittest.main()
