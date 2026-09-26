"""Per-dataset CRS isolation across loading, ingest, graph snapshots and export."""

import json
import unittest
from pathlib import Path
from unittest.mock import mock_open, patch

import networkx as nx
from pyproj import Geod

from core.gis.gis_loader import (
    VALID_NODE_TYPES, load_gis, wgs84_to_working, working_to_wgs84,
)
from core.gis.building_association import build_building_lookup, SpatialIndex
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.observation.geolocator import ManualOverrideGeoLocator
from core.observation.ingest import ingest_ai_analysis_result
from core.observation.observation_log import ObservationLog
from core.observation.state_update import apply_observations
from core.overlay.gis_overlay import build_node_overlay
from tests.test_ingest import make_realistic_event


def load_features(features):
    raw = json.dumps({"type": "FeatureCollection", "features": features})
    with patch("builtins.open", mock_open(read_data=raw)):
        return load_gis("in-memory.geojson")


def point_feature(lon, lat, node_type="road"):
    return {"type": "Feature", "properties": {
        "feature_class": "infrastructure", "node_type": node_type,
    }, "geometry": {"type": "Point", "coordinates": [lon, lat]}}


def louisiana_data():
    return load_features([{
        "type": "Feature", "properties": {"highway": "residential", "lanes": "2"},
        "geometry": {"type": "LineString", "coordinates": [[-90, 30], [-89.999, 30]]},
    }, {
        "type": "Feature", "properties": {"building": "yes"},
        "geometry": {"type": "Polygon", "coordinates": [[
            [-90, 30.0001], [-89.9998, 30.0001], [-89.9998, 30.0003],
            [-90, 30.0003], [-90, 30.0001],
        ]]},
    }])


class GISCRSTests(unittest.TestCase):
    def test_utm_selection(self):
        for lon, lat, expected in [
            (77.6974, 12.7124, "EPSG:32643"),
            (-90, 30, "EPSG:32616"), (151, -33, "EPSG:32756"),
            (180, 0, "EPSG:32660"), (-180, 0, "EPSG:32601"),
            (6, 60, "EPSG:32632"), (15, 78, "EPSG:32633"),
        ]:
            with self.subTest(lon=lon, lat=lat):
                data = load_features([point_feature(lon, lat)])
                self.assertEqual(data.working_crs, expected)
                p = data.infra_points[0].geometry
                restored = working_to_wgs84(p.x, p.y, data.working_crs)
                self.assertAlmostEqual(restored[0], lon, places=7)
                self.assertAlmostEqual(restored[1], lat, places=7)

    def test_invalid_extent_and_empty_data(self):
        for lon, lat in [(0, 85), (0, -81), (181, 0)]:
            with self.subTest(lon=lon, lat=lat), self.assertRaises(ValueError):
                load_features([point_feature(lon, lat)])
        with self.assertRaisesRegex(ValueError, "Antimeridian"):
            load_features([point_feature(-179, 0), point_feature(179, 0)])
        self.assertEqual(load_features([]).working_crs, "EPSG:32643")

    def test_six_type_validation(self):
        features = [point_feature(77, 12, t) for t in VALID_NODE_TYPES]
        for node_type in [None, "factory", "building", 1]:
            feature = point_feature(0, 90, node_type)
            feature["geometry"] = None
            features.append(feature)
        data = load_features(features)
        self.assertEqual({p.node_type for p in data.infra_points}, VALID_NODE_TYPES)
        self.assertEqual(data.excluded_infra_count, 4)

    def test_demo_and_louisiana_are_independent(self):
        fixture = Path(__file__).resolve().parents[1] / "core/gis/fixtures/demo_gis.geojson"
        demo = load_gis(str(fixture))
        graph, _ = build_graph_from_gis(demo, seed=42)
        original_overlay = build_node_overlay(graph)
        louisiana = louisiana_data()
        self.assertEqual(demo.working_crs, "EPSG:32643")
        self.assertEqual(louisiana.working_crs, "EPSG:32616")
        self.assertEqual(graph.number_of_nodes(), 17)
        self.assertEqual(build_node_overlay(graph), original_overlay)
        for p in demo.infra_points:
            lon, lat = working_to_wgs84(p.geometry.x, p.geometry.y)
            self.assertEqual(wgs84_to_working(lon, lat),
                             wgs84_to_working(lon, lat, demo.working_crs))
        road = louisiana.road_segments[0].geometry
        _, _, geodesic_m = Geod(ellps="WGS84").inv(-90, 30, -89.999, 30)
        self.assertAlmostEqual(road.length / geodesic_m, 1, delta=0.002)

    def test_ingest_snapshot_and_overlay_share_crs(self):
        data = louisiana_data()
        graph, mapping = build_graph_from_gis(data, seed=42)
        index = SpatialIndex(data, build_building_lookup(data, mapping), mapping)
        log = ObservationLog()
        event = make_realistic_event("crs", "Severe", 0.8, 1000)
        result = ingest_ai_analysis_result(
            event, ManualOverrideGeoLocator(), index, log,
            {"override_lon": -89.9999, "override_lat": 30.0002},
        )
        self.assertEqual(result.detections_ingested, 1)
        record = log.observations_for_node(mapping["bldg_1"])[0]
        self.assertEqual(record.building_id, "bldg_1")
        self.assertEqual(record.matched_gis_source_id, "bldg_1")
        self.assertEqual(record.node_id, mapping["bldg_1"])
        self.assertEqual((record.working_x, record.working_y),
                         wgs84_to_working(-89.9999, 30.0002, data.working_crs))
        snapshot = apply_observations(graph, log, now=1000)
        self.assertEqual(snapshot.graph["working_crs"], data.working_crs)
        overlay = build_node_overlay(snapshot)
        for feature in overlay["features"]:
            lon, lat = feature["geometry"]["coordinates"]
            self.assertTrue(-90.001 < lon < -89.998)
            self.assertTrue(29.999 < lat < 30.001)
            x, y = wgs84_to_working(lon, lat, data.working_crs)
            pos = graph.nodes[int(feature["properties"]["id"])]["pos"]
            self.assertAlmostEqual(x, pos[0], places=5)
            self.assertAlmostEqual(y, pos[1], places=5)

    def test_overlay_requires_crs_metadata(self):
        with self.assertRaisesRegex(ValueError, "working_crs"):
            build_node_overlay(nx.DiGraph())


if __name__ == "__main__":
    unittest.main()
