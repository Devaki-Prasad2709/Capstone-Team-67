"""Regression checks for GIS lane parsing and provisional building nodes."""

import unittest

from shapely.geometry import LineString, Point, Polygon

from core.gis.gis_loader import Building, GISData, InfraPoint, RoadSegment
from core.graphs.gis_graph_builder import build_graph_from_gis


class GISGraphBuilderTests(unittest.TestCase):
    def test_lane_capacity(self):
        cases = [(1, 0.4), (2, 0.8), (4, 1.0), ("1", 0.4),
                 (" 1.5 ", 0.6), ("4", 1.0)]
        invalid = [None, "", "unknown", "2;3", [], {}, True, False,
                   0, -1, "0", "-2", "nan", "inf", float("nan"), float("inf")]
        cases.extend((value, 0.8) for value in invalid)
        for lanes, expected in cases:
            with self.subTest(lanes=lanes):
                data = GISData("test", road_segments=[
                    RoadSegment("r", "road", LineString([(0, 0), (10, 0)]), lanes)
                ])
                graph, mapping = build_graph_from_gis(data, seed=42)
                self.assertAlmostEqual(graph.nodes[mapping["r"]]["capacity"], expected)

    def test_buildings_preserve_graph_contract_and_existing_ids(self):
        data = GISData("test", infra_points=[
            InfraPoint("p", "telecom", "provider", Point(0, 0))
        ], road_segments=[
            RoadSegment("r", "road", LineString([(0, 0), (10, 0)]), "2")
        ])
        baseline, original_ids = build_graph_from_gis(data, seed=42)
        data.buildings.append(Building(
            "b", "building", Polygon([(10, 10), (12, 10), (12, 12), (10, 12)])
        ))
        graph, mapping = build_graph_from_gis(data, seed=42)
        self.assertEqual(mapping, {**original_ids, "b": 2})
        self.assertEqual(list(graph), [0, 1, 2])
        for node in baseline:
            self.assertEqual(graph.nodes[node], baseline.nodes[node])
        building = graph.nodes[mapping["b"]]
        self.assertEqual(building["type"], "social")
        self.assertEqual(building["pos"], (11.0, 11.0))
        self.assertEqual(building["capacity"], 0.6)
        self.assertEqual(set(building), set(baseline.nodes[0]))
        self.assertEqual(graph.edges[1, 2]["edge_type"], 0)
        self.assertEqual(graph.edges[2, 1]["edge_type"], 0)
        self.assertEqual(graph.edges[0, 2]["edge_type"], 1)
        for _, _, attrs in graph.edges(data=True):
            self.assertEqual(set(attrs), {"weight", "delay", "edge_type"})


if __name__ == "__main__":
    unittest.main()
