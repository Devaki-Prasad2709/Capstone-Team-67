from __future__ import annotations

import unittest
from pathlib import Path

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.integration.tgnn_predictor import predict_node_risk
from core.observation.observation_log import ObservationLog, ObservationRecord
from core.observation.state_update import apply_observations


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "core" / "gis" / "fixtures" / "demo_gis.geojson"


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


if __name__ == "__main__":
    unittest.main()
