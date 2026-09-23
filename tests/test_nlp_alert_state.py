import copy
import json
import os
from pathlib import Path
import unittest

from core.gis.gis_loader import load_gis, wgs84_to_working
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.nlp.alert_state import AlertStore, InvalidTransition
from core.nlp.landmark_alert_resolver import ResolvedPriorityAlert, resolve_alert


class AlertStateTests(unittest.TestCase):
    def setUp(self):
        fixture = Path(__file__).resolve().parents[1] / "core/gis/fixtures/demo_gis.geojson"
        self.data = load_gis(str(fixture))
        self.graph, self.ids = build_graph_from_gis(self.data, seed=42)
        self.before = copy.deepcopy(self.graph)
        self.store = AlertStore(self.graph, clock=lambda: "2026-09-23T12:00:00+00:00")
        self.resolved = resolve_alert("people trapped near General Hospital", self.data, self.ids)

    def create(self, alert_id="a", resolved=None):
        return self.store.create_alert(alert_id, resolved or self.resolved,
                                       source="social", source_id="post-1")

    def tearDown(self):
        self.assertEqual(self.graph.graph, self.before.graph)
        self.assertEqual(dict(self.graph.nodes(data=True)), dict(self.before.nodes(data=True)))
        self.assertEqual(dict(self.graph.edges), dict(self.before.edges))

    def test_pending_then_confirmed_hotspot(self):
        alert = self.create()
        self.assertEqual(alert["status"], "PENDING_REVIEW")
        self.assertEqual(self.store.confirmed_hotspots()["features"], [])
        approved = self.store.approve_alert("a", responder_id="responder-7")
        self.assertEqual(approved["status"], "CONFIRMED")
        feature = self.store.confirmed_hotspots()["features"][0]
        props = feature["properties"]
        self.assertEqual(props["nlp_confidence"], self.resolved.confidence)
        self.assertTrue(props["responder_confirmation"]["confirmed"])
        self.assertEqual(props["source_id"], "post-1")
        self.assertEqual(props["original_text"], self.resolved.raw_text)
        self.assertEqual(len(props["history"]), 2)
        lon, lat = feature["geometry"]["coordinates"]
        x, y = wgs84_to_working(lon, lat, self.graph.graph["working_crs"])
        px, py = self.graph.nodes[self.resolved.node_id]["pos"]
        self.assertAlmostEqual(x, px, places=5)
        self.assertAlmostEqual(y, py, places=5)
        json.dumps(self.store.dashboard_state(), allow_nan=False)

    def test_false_stays_in_history_not_hotspots(self):
        self.create()
        alert = self.store.report_false("a", responder_id="responder-7")
        self.assertEqual(alert["status"], "REPORTED_FALSE")
        state = self.store.dashboard_state()
        self.assertEqual(len(state["reported_false_alerts"]), 1)
        self.assertEqual(state["pending_alerts"], [])
        self.assertEqual(state["confirmed_hotspots"]["features"], [])
        self.assertEqual(alert["report"], dict(self.resolved))

    def test_terminal_transitions_and_duplicates_rejected(self):
        for first in ("approve_alert", "report_false"):
            self.create(first)
            getattr(self.store, first)(first, responder_id="r")
            before = self.store.get_alert(first)
            for second in ("approve_alert", "report_false"):
                with self.assertRaises(InvalidTransition):
                    getattr(self.store, second)(first, responder_id="r")
                self.assertEqual(before, self.store.get_alert(first))
            with self.assertRaises(ValueError):
                self.create(first)
        with self.assertRaises(KeyError):
            self.store.approve_alert("missing", responder_id="r")

    def test_unresolved_or_missing_node_cannot_be_confirmed(self):
        for node in (None, 99999):
            key = str(node)
            self.create(key, ResolvedPriorityAlert("trapped", True, node, "unresolved", None, 0))
            with self.assertRaises(ValueError):
                self.store.approve_alert(key, responder_id="r")
            self.assertEqual(self.store.get_alert(key)["status"], "PENDING_REVIEW")
            self.store.report_false(key, responder_id="r")

    def test_external_mutation_cannot_overwrite_evidence(self):
        created = self.create()
        created["report"]["raw_text"] = "changed"
        self.resolved.raw_text = "changed again"
        self.assertEqual(self.store.get_alert("a")["report"]["raw_text"],
                         "people trapped near General Hospital")
        with self.assertRaises(ValueError):
            self.store.approve_alert("a", responder_id="")
        self.assertEqual(self.store.get_alert("a")["status"], "PENDING_REVIEW")

    @unittest.skipUnless(os.environ.get("SPACENET8_TEST_ROOT"), "Set SPACENET8_TEST_ROOT for Louisiana smoke")
    def test_real_louisiana(self):
        path = Path(os.environ["SPACENET8_TEST_ROOT"]) / "annotations/0_10_2.geojson"
        data = load_gis(str(path))
        graph, ids = build_graph_from_gis(data, seed=42)
        original = copy.deepcopy(graph)
        reference = wgs84_to_working(-90.02204402568451, 29.73879868866008, data.working_crs)
        resolved = resolve_alert("People are trapped near the road and need urgent rescue",
                                 data, ids, reference_pos=reference)
        self.assertEqual((resolved.node_id, resolved.confidence), (6, 0.4))
        store = AlertStore(graph, clock=lambda: "2026-09-23T12:00:00+00:00")
        store.create_alert("louisiana-1", resolved, source="social", source_id="report-1")
        store.approve_alert("louisiana-1", responder_id="responder-test")
        feature = store.confirmed_hotspots()["features"][0]
        lon, lat = feature["geometry"]["coordinates"]
        self.assertTrue(-90.03 < lon < -90.01 and 29.73 < lat < 29.75)
        x, y = wgs84_to_working(lon, lat, graph.graph["working_crs"])
        px, py = graph.nodes[6]["pos"]
        self.assertAlmostEqual(x, px, places=5)
        self.assertAlmostEqual(y, py, places=5)
        self.assertEqual(graph.graph, original.graph)
        self.assertEqual(dict(graph.nodes(data=True)), dict(original.nodes(data=True)))
        self.assertEqual(dict(graph.edges), dict(original.edges))
        print("REAL HOTSPOT:", json.dumps(feature, allow_nan=False))


if __name__ == "__main__":
    unittest.main()
