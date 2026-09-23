"""Analytical alignment checks and fail-closed GeoTIFF parsing tests."""

import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, TiffImagePlugin

from core.satellite.broad_area_classifier import classify_area
from core.satellite.spacenet_adapter import (
    GeoGrid, _read_grid, _sample_post, classify_spacenet_pair,
)


def tagged_image(overrides=None):
    tags = TiffImagePlugin.ImageFileDirectory_v2()
    values = {33550: (0.001, 0.001, 0.0),
              33922: (2.0, 3.0, 0.0, -90.0, 30.0, 0.0),
              34735: (1, 1, 0, 4, 1024, 0, 1, 2, 1025, 0, 1, 1,
                      2048, 0, 1, 4326, 2054, 0, 1, 9102)}
    values.update(overrides or {})
    for key, value in values.items():
        if value is not None:
            tags[key] = value
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10), (100, 100, 100)).save(buffer, format="TIFF", tiffinfo=tags)
    buffer.seek(0)
    return Image.open(buffer)


class SpaceNetAdapterTests(unittest.TestCase):
    def test_pixel_area_transform_with_nonzero_tiepoint(self):
        with tagged_image() as image:
            grid = _read_grid(image)
        self.assertAlmostEqual(grid.west, -90.002)
        self.assertAlmostEqual(grid.north, 30.003)
        self.assertAlmostEqual(grid.bounds[1], 29.993)

    def test_unsupported_metadata_rejected(self):
        cases = [{33550: None}, {33922: None}, {34735: None},
                 {33550: (-1., 1., 0.)}, {34264: tuple(float(i) for i in range(16))},
                 {34735: (1, 1, 0, 2, 1024, 0, 1, 2, 1025, 0, 1, 2)},
                 {34735: (1, 1, 0, 4, 1024, 0, 1, 1, 1025, 0, 1, 1,
                           2048, 0, 1, 4326, 2054, 0, 1, 9102)}]
        for tags in cases:
            with self.subTest(tags=tags), tagged_image(tags) as image:
                with self.assertRaises(ValueError):
                    _read_grid(image)

    def test_bilinear_scale_and_translation_preserve_world_ramp(self):
        source = GeoGrid(8, 8, 0, 10, 1, 1)
        target = GeoGrid(7, 6, 1.25, 8.75, 0.5, 0.5)
        x = np.arange(8) + 0.5
        y = 10 - (np.arange(8) + 0.5)
        ramp = 5 * x[None, :] + 3 * y[:, None]
        result, valid = _sample_post(target, source, ramp, np.ones((8, 8), bool))
        tx = 1.25 + (np.arange(7) + 0.5) * 0.5
        ty = 8.75 - (np.arange(6) + 0.5) * 0.5
        np.testing.assert_allclose(result, 5 * tx[None, :] + 3 * ty[:, None])
        self.assertTrue(valid.all())

    def test_invalid_support_and_no_extrapolation(self):
        source = GeoGrid(3, 3, 0, 3, 1, 1)
        target = GeoGrid(4, 4, -0.25, 3.25, 1, 1)
        mask = np.ones((3, 3), bool)
        mask[1, 1] = False
        _, valid = _sample_post(target, source, np.ones((3, 3)), mask)
        self.assertFalse(valid[0].any())
        self.assertFalse(valid[:, 0].any())
        self.assertFalse(valid[1, 1])

    def test_geojson_pixel_boundaries_and_low_coverage(self):
        grid = GeoGrid(7, 5, -90, 30, 0.001, 0.001)
        array = np.full((5, 7), 100.)
        mask = np.ones((5, 7), bool)
        mask[0, 0] = False
        with patch("core.satellite.spacenet_adapter._load", return_value=(grid, array, mask)):
            result = classify_spacenet_pair("pre", "post", grid_size=2)
        self.assertEqual(len(result["features"]), 4)
        self.assertIsNone(result["features"][0]["properties"]["diff_score"])
        self.assertEqual(result["features"][1]["properties"]["diff_score"], 0)
        ring = result["features"][0]["geometry"]["coordinates"][0]
        self.assertEqual(ring[1][0], -90 + 3 * 0.001)
        self.assertEqual(ring[0][1], 30 - 2 * 0.001)

    def test_nonoverlap_rejected(self):
        a = (GeoGrid(2, 2, -90, 30, .001, .001), np.ones((2, 2)), np.ones((2, 2), bool))
        b = (GeoGrid(2, 2, -80, 30, .001, .001), a[1], a[2])
        with patch("core.satellite.spacenet_adapter._load", side_effect=[a, b]):
            with self.assertRaisesRegex(ValueError, "no geographic overlap"):
                classify_spacenet_pair("pre", "post", 1)

    def test_black_and_declared_nodata_mask(self):
        from core.satellite.spacenet_adapter import _load
        with tagged_image({42113: "100"}) as image:
            with patch("core.satellite.spacenet_adapter.Image.open", return_value=image):
                _, _, valid = _load("test")
        self.assertFalse(valid.any())
        with tagged_image() as image:
            image.load()
            image.paste((0, 0, 0), (0, 0, 10, 10))
            with patch("core.satellite.spacenet_adapter.Image.open", return_value=image):
                _, _, valid = _load("test")
        self.assertFalse(valid.any())

    def test_generic_classifier_still_rejects_mismatched_sizes(self):
        with patch("core.satellite.broad_area_classifier.Image.open", side_effect=[
            Image.new("RGB", (10, 10)), Image.new("RGB", (8, 8)),
        ]):
            with self.assertRaisesRegex(ValueError, "sizes differ"):
                classify_area("pre", "post", (-90, 29, -89, 30), 2)

    @unittest.skipUnless(os.environ.get("SPACENET8_TEST_ROOT"), "Set SPACENET8_TEST_ROOT for real-data smoke test")
    def test_real_louisiana_pair(self):
        from pyproj import Transformer
        from shapely.geometry import Point, shape
        from core.satellite.spacenet_adapter import read_geotiff_grid

        root = Path(os.environ["SPACENET8_TEST_ROOT"])
        pre = root / "PRE-event/105001001A0FFC00_0_10_2.tif"
        post = root / "POST-event/10300100C46F5900_0_10_2.tif"
        a, b = read_geotiff_grid(pre), read_geotiff_grid(post)
        self.assertEqual((a.width, a.height), (1300, 1300))
        self.assertEqual((b.width, b.height), (1114, 1114))
        self.assertLessEqual(b.bounds[0], a.bounds[0])
        self.assertLessEqual(b.bounds[1], a.bounds[1])
        self.assertGreaterEqual(b.bounds[2], a.bounds[2])
        self.assertGreaterEqual(b.bounds[3], a.bounds[3])
        result = classify_spacenet_pair(pre, post)
        self.assertEqual(len(result["features"]), 16)
        polygons = [shape(f["geometry"]) for f in result["features"]]
        self.assertTrue(all(p.is_valid for p in polygons))
        building = Point(-90.02204402568451, 29.73879868866008)
        self.assertEqual(sum(p.covers(building) for p in polygons), 1)
        transform = Transformer.from_crs("EPSG:4326", "EPSG:32615", always_xy=True)
        x0, y0 = transform.transform(a.bounds[0], a.bounds[1])
        x1, y1 = transform.transform(a.bounds[2], a.bounds[3])
        self.assertTrue(500 < abs(x1 - x0) < 700)
        self.assertTrue(600 < abs(y1 - y0) < 750)
        scored = 0
        for feature in result["features"]:
            props = feature["properties"]
            if props["diff_score"] is None:
                self.assertLess(props["valid_fraction"], 0.95)
                self.assertEqual(props["severity"], "unknown")
            else:
                scored += 1
                self.assertTrue(0 <= props["diff_score"] <= 1)
                self.assertGreaterEqual(props["valid_fraction"], 0.95)
        self.assertGreater(scored, 0)
        print(f"Real smoke: {scored}/16 scored cells; valid coverage={result['metadata']['valid_fraction']:.6%}; known GIS building covered")


if __name__ == "__main__":
    unittest.main()
