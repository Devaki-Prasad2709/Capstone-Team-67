from __future__ import annotations

import unittest
import copy
import json
import math
from datetime import datetime
from pathlib import Path

import torch

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.integration.pyg_bridge import build_graph_sequence
from core.integration.tgnn_predictor import (
    predict_gis_node_risk,
    predict_node_risk,
    predict_node_risk_report,
)
from core.observation.observation_log import ObservationLog, ObservationRecord
from core.observation.state_update import apply_observations
from core.observation.state_update import recompute_node_state
from core.observation.telemetry import apply_telemetry_event
from tgnn.utils.helpers import NODE_FEATURE_ORDER, nx_to_pyg


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "core" / "gis" / "fixtures" / "demo_gis.geojson"
SCENARIO_GIS = ROOT / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"
SCENARIO_TELEMETRY = ROOT / "scenarios/louisiana_east_flood/telemetry.json"


class TGNNPredictorTests(unittest.TestCase):
    def test_checkpoint_predicts_risk_for_real_gis_node_ids(self) -> None:
        gis_data = load_gis(str(FIXTURE))
        initial, _ = build_graph_from_gis(gis_data, seed=42)
        target = next(iter(initial.nodes))
        log = ObservationLog()
        log.add(
            ObservationRecord(
                observation_id="checkpoint-test",
                timestamp=100.0,
                frame_id="frame-1",
                track_id=None,
                source="drone",
                class_id=1,
                class_name="Severe",
                confidence=0.9,
                bbox=(1.0, 2.0, 3.0, 4.0),
                latitude=12.71,
                longitude=77.69,
                working_x=initial.nodes[target]["pos"][0],
                working_y=initial.nodes[target]["pos"][1],
                building_id=None,
                node_id=target,
            )
        )
        updated = apply_observations(initial, log, now=100.0)

        risks = predict_node_risk([initial, updated])

        self.assertEqual(list(risks), list(initial.nodes))
        self.assertTrue(all(0.0 <= value <= 1.0 for value in risks.values()))
        self.assertGreater(len({round(value, 6) for value in risks.values()}), 1)

    def test_changed_node_order_is_rejected(self) -> None:
        gis_data = load_gis(str(FIXTURE))
        initial, _ = build_graph_from_gis(gis_data, seed=42)
        reordered = initial.__class__()
        for node_id in reversed(list(initial.nodes)):
            reordered.add_node(node_id, **initial.nodes[node_id])
        reordered.add_edges_from(initial.edges(data=True))

        with self.assertRaisesRegex(ValueError, "different node order"):
            predict_node_risk([initial, reordered])

    def test_feature_contract_normalization_dimensions_and_finiteness(self) -> None:
        data = load_gis(str(SCENARIO_GIS))
        initial, ids = build_graph_from_gis(data, seed=42)
        changed = copy.deepcopy(initial)
        changed.graph["snapshot_timestamp"] = 1.0
        target = ids["osm-way-791288888"]
        changed.nodes[target]["damage"] = 0.35
        recompute_node_state(changed.nodes[target], timestamp=1.0)

        raw = build_graph_sequence([initial, changed], nx_to_pyg, normalize_positions=False)
        normalized = build_graph_sequence([initial, changed], nx_to_pyg)
        self.assertEqual(NODE_FEATURE_ORDER, (
            "x_pos", "y_pos", "load", "capacity", "damage", "stress", "status"
        ))
        for raw_data, data_tensor in zip(raw, normalized):
            self.assertEqual(tuple(data_tensor.x.shape), (21, 7))
            self.assertEqual(tuple(data_tensor.edge_index.shape), (2, 322))
            self.assertEqual(tuple(data_tensor.edge_attr.shape), (322, 3))
            self.assertEqual(tuple(data_tensor.node_type.shape), (21,))
            self.assertTrue(torch.isfinite(data_tensor.x).all())
            self.assertGreaterEqual(float(data_tensor.x[:, :2].min()), 0.0)
            self.assertLessEqual(float(data_tensor.x[:, :2].max()), 1.0)
            self.assertTrue(torch.equal(raw_data.x[:, 2:], data_tensor.x[:, 2:]))
            self.assertTrue(torch.equal(raw_data.edge_index, data_tensor.edge_index))
            self.assertTrue(torch.equal(raw_data.edge_attr, data_tensor.edge_attr))

    def test_louisiana_ranking_damage_sensitivity_and_recovery_response(self) -> None:
        data = load_gis(str(SCENARIO_GIS))
        initial, ids = build_graph_from_gis(data, seed=42)
        definitions = json.loads(SCENARIO_TELEMETRY.read_text(encoding="utf-8"))["events"]

        def event(item):
            return {
                "id": item["event_id"],
                "timestamp": datetime.fromisoformat(
                    item["scenario_timestamp"].replace("Z", "+00:00")
                ).timestamp(),
                "target_id": item["target_id"],
                "load": item["load"],
                "capacity": item["capacity"],
                "damage": item["damage"],
                "event_type": item["event_type"],
            }

        degraded = apply_telemetry_event(initial, ids, event(definitions[0]))
        recovered = apply_telemetry_event(degraded, ids, event(definitions[1]))
        report = predict_node_risk_report([initial, degraded, recovered])
        target = ids["osm-way-791288888"]
        target_rows = [snapshot["nodes"][target] for snapshot in report["timeline"]]

        self.assertEqual(report["feature_order"], list(NODE_FEATURE_ORDER))
        self.assertEqual(report["model_input_features"], list(NODE_FEATURE_ORDER[:-1]))
        self.assertEqual(report["training_target"], "status")
        self.assertEqual(report["training_target_encoding"], {"operational": 1, "failed": 0})
        self.assertFalse(report["calibrated_probability"])
        self.assertEqual(
            report["checkpoint_sha256"],
            "ad40a02e91cfe414da23f585dcf237d7fd2b5f646f3ebc47c20cb7d73640cc88",
        )
        self.assertFalse(report["checkpoint_manifest"]["calibrated_probability"])
        self.assertTrue(all(
            math.isfinite(node["failure_risk_score"])
            for snapshot in report["timeline"] for node in snapshot["nodes"]
        ))
        self.assertTrue(all(
            math.isclose(
                node["operational_score"] + node["failure_risk_score"], 1.0,
                abs_tol=1e-6,
            )
            for snapshot in report["timeline"] for node in snapshot["nodes"]
        ))
        self.assertTrue(all(
            node["tensor_row"] == row
            and node["graph_node_id"] == list(initial.nodes)[row]
            and node["gis_source_id"] == initial.nodes[node["graph_node_id"]]["gis_source_id"]
            for snapshot in report["timeline"]
            for row, node in enumerate(snapshot["nodes"])
        ))

        baseline, damage, recovery = target_rows
        self.assertGreater(damage["failure_risk_score"], baseline["failure_risk_score"])
        self.assertEqual(damage["risk_rank"], 1)
        self.assertLess(damage["risk_rank"], baseline["risk_rank"])
        self.assertLess(recovery["failure_risk_score"], damage["failure_risk_score"])
        self.assertEqual(
            damage["gis_source_id"], "osm-way-791288888"
        )
        mapped = predict_gis_node_risk([initial, degraded, recovered])
        self.assertEqual(set(mapped), set(ids))
        self.assertAlmostEqual(
            mapped["osm-way-791288888"], recovery["failure_risk_score"]
        )

    def test_isolated_damage_and_stress_increase_relative_failure_score(self) -> None:
        data = load_gis(str(SCENARIO_GIS))
        initial, ids = build_graph_from_gis(data, seed=42)
        target = ids["osm-way-791288888"]
        damaged = copy.deepcopy(initial)
        damaged.graph["snapshot_timestamp"] = 1.0
        damaged.nodes[target]["damage"] = 0.35
        recompute_node_state(damaged.nodes[target], timestamp=1.0)
        report = predict_node_risk_report([initial, damaged])
        before = report["timeline"][0]["nodes"][target]
        after = report["timeline"][1]["nodes"][target]
        self.assertGreater(damaged.nodes[target]["stress"], initial.nodes[target]["stress"])
        self.assertGreater(after["failure_risk_score"], before["failure_risk_score"])
        self.assertEqual(after["risk_rank"], 1)

    def test_temporal_order_and_topology_changes_are_rejected(self) -> None:
        data = load_gis(str(SCENARIO_GIS))
        initial, _ = build_graph_from_gis(data, seed=42)
        first = copy.deepcopy(initial)
        second = copy.deepcopy(initial)
        first.graph["snapshot_timestamp"] = 2.0
        second.graph["snapshot_timestamp"] = 1.0
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            predict_node_risk([first, second])
        topology_changed = copy.deepcopy(initial)
        topology_changed.remove_edge(*next(iter(topology_changed.edges)))
        with self.assertRaisesRegex(ValueError, "topology"):
            predict_node_risk([initial, topology_changed])


if __name__ == "__main__":
    unittest.main()
