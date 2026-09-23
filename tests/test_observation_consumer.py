"""Observation adapter tests using real core APIs and in-memory Kafka doubles."""

import copy
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ai.computer_vision.contracts import result_event
from consumers import observation_consumer as adapter
from core.observation.geolocator import NullGeoLocator

FIXTURE = "core/gis/fixtures/demo_gis.geojson"
TEST_LOCATION = (12.7106, 77.6949)


class ObservationConsumerTests(unittest.TestCase):
    def setUp(self):
        with redirect_stdout(io.StringIO()), patch.object(adapter.logger, "warning"):
            self.state = adapter.initialize_state(FIXTURE, TEST_LOCATION)
        self.event = result_event(
            {"frame_id": "consumer_test", "source": "drone", "timestamp": time.time()},
            "analyzed",
            [{"class_id": 2, "class_name": "Debris", "confidence": 0.3, "bbox": [1, 2, 3, 4]}],
        )

    def payload(self, event=None):
        return json.dumps(self.event if event is None else event).encode("utf-8")

    def message(self, value, offset=0):
        return SimpleNamespace(value=value, topic=adapter.RESULT_TOPIC, partition=0, offset=offset)

    def test_real_ingest_updates_graph_and_passes_event_unchanged(self):
        baseline = self.state.graph
        expected = copy.deepcopy(self.event)
        with patch.object(adapter, "ingest_ai_analysis_result", wraps=adapter.ingest_ai_analysis_result) as ingest:
            with self.assertLogs(adapter.logger, level="INFO") as logs:
                result = adapter.process_message(self.payload(), self.state)
            self.assertEqual(ingest.call_args.args[0], expected)
        self.assertEqual(result.detections_ingested, 1)
        self.assertEqual(result.detections_skipped_no_node, 0)
        self.assertEqual(result.touched_node_ids, [8])
        self.assertEqual(len(self.state.observation_log.observations_for_node(8)), 1)
        self.assertAlmostEqual(self.state.graph.nodes[8]["damage"], 0.3, places=4)
        self.assertGreater(self.state.graph.nodes[8]["stress"], baseline.nodes[8]["stress"])
        self.assertEqual(baseline.nodes[8]["damage"], 0)
        self.assertEqual(dict(baseline.edges), dict(self.state.graph.edges))
        for field in ["frame_id", "detections_seen", "detections_ingested", "detections_skipped_no_node", "touched_node_ids"]:
            self.assertIn(field + "=", logs.output[0])

    def test_bad_messages_do_not_stop_loop_or_partially_append(self):
        bad_detection = copy.deepcopy(self.event)
        bad_detection["detections"].append({"class_id": 1})
        messages = [self.message(value, i) for i, value in enumerate([
            b'\xff', b'{', b'[]', b'{}', self.payload(bad_detection), self.payload(),
        ])]
        with self.assertLogs(adapter.logger, level="ERROR") as logs:
            adapter.consume_messages(messages, self.state)
        self.assertEqual(len(logs.output), 5)
        self.assertEqual(len(self.state.observation_log.observations_for_node(8)), 1)
        self.assertGreater(self.state.graph.nodes[8]["damage"], 0)

    def test_invalid_numbers_rejected_before_ingestion(self):
        for field, value in [("confidence", float("nan")), ("confidence", 1.1), ("bbox", [1, 2, 3, float("inf")])]:
            event = copy.deepcopy(self.event)
            event["detections"][0][field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                adapter.process_message(self.payload(event), self.state)
        self.event["source_timestamp"] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite epoch"):
            adapter.process_message(self.payload(), self.state)
        self.assertEqual(self.state.observation_log.all_observed_node_ids(), [])

    def test_non_analyzed_and_no_node(self):
        skipped = copy.deepcopy(self.event)
        skipped["status"] = "skipped"
        self.assertEqual(adapter.process_message(self.payload(skipped), self.state).detections_ingested, 0)
        with patch.object(self.state.spatial_index, "resolve", return_value={"building_id": None, "node_id": None}):
            result = adapter.process_message(self.payload(), self.state)
        self.assertEqual(result.detections_skipped_no_node, 1)
        self.assertEqual(self.state.observation_log.all_observed_node_ids(), [])

    def test_geolocation_failure_does_not_stop_next_message(self):
        locate = self.state.geolocator.locate
        calls = 0

        def fail_once(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("geolocation failure")
            return locate(*args, **kwargs)

        with patch.object(self.state.geolocator, "locate", side_effect=fail_once):
            with self.assertLogs(adapter.logger, level="ERROR"):
                adapter.consume_messages([self.message(self.payload()), self.message(self.payload(), 1)], self.state)
        self.assertEqual(calls, 2)
        self.assertEqual(len(self.state.observation_log.observations_for_node(8)), 1)

    def test_null_geolocation_fails_without_fabricating_observations(self):
        with redirect_stdout(io.StringIO()):
            state = adapter.initialize_state(FIXTURE)
        self.assertIsInstance(state.geolocator, NullGeoLocator)
        self.assertIsNone(state.drone_telemetry)
        with self.assertRaises(NotImplementedError):
            adapter.process_message(self.payload(), state)
        self.assertEqual(state.observation_log.all_observed_node_ids(), [])

    def test_invalid_configuration(self):
        for path in ["", "core/gis/fixtures"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                adapter.initialize_state(path)
        with self.assertRaisesRegex(ValueError, "WGS84"):
            adapter.initialize_state(FIXTURE, (float("nan"), 0))

    def test_default_cli_never_connects_without_real_geolocation(self):
        with patch.object(adapter, "configure_logging"), patch.object(adapter, "KafkaConsumer") as kafka:
            with redirect_stdout(io.StringIO()), self.assertLogs(adapter.logger, level="ERROR"):
                with self.assertRaises(SystemExit) as error:
                    adapter.main(["--gis-path", FIXTURE])
            self.assertEqual(error.exception.code, 1)
            kafka.assert_not_called()

    def test_no_brokers_handling(self):
        with patch.object(adapter, "configure_logging"), patch.object(adapter, "initialize_state", return_value=self.state):
            with patch.object(adapter, "KafkaConsumer", side_effect=adapter.NoBrokersAvailable):
                with self.assertLogs(adapter.logger, level="ERROR") as logs, self.assertRaises(SystemExit) as error:
                    adapter.main(["--gis-path", FIXTURE, "--test-manual-location", *map(str, TEST_LOCATION)])
        self.assertEqual(error.exception.code, 1)
        self.assertIn("Kafka is unavailable", logs.output[0])

    def test_kafka_configuration_and_close(self):
        consumer = Mock()
        consumer.__iter__ = Mock(return_value=iter([self.message(self.payload())]))
        with patch.object(adapter, "configure_logging"), patch.object(adapter, "initialize_state", return_value=self.state):
            with patch.object(adapter, "KafkaConsumer", return_value=consumer) as kafka:
                adapter.main(["--gis-path", FIXTURE, "--test-manual-location", *map(str, TEST_LOCATION)])
        self.assertEqual(kafka.call_args.args, ("ai-analysis-results",))
        self.assertEqual(kafka.call_args.kwargs["bootstrap_servers"], adapter.settings.kafka_bootstrap_servers.split(","))
        self.assertEqual(kafka.call_args.kwargs["group_id"], "disaster-observation-test-v1")
        self.assertEqual(kafka.call_args.kwargs["auto_offset_reset"], "latest")
        self.assertTrue(kafka.call_args.kwargs["enable_auto_commit"])
        consumer.close.assert_called_once()


if __name__ == "__main__":
    unittest.main()
