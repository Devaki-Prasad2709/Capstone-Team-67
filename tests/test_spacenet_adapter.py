"""Analytical alignment checks and fail-closed GeoTIFF parsing tests."""

import io
import unittest
from unittest.mock import patch

from PIL import Image, TiffImagePlugin

from core.satellite.broad_area_classifier import classify_area
from core.satellite.spacenet_adapter import (
    _read_grid, classify_spacenet_pair,
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

    def test_public_wrapper_delegates_to_universal_boundary_and_generic_classifier(self):
        expected = {"type": "FeatureCollection", "features": []}
        with (
            patch("core.satellite.spacenet_adapter.normalize_satellite_pair", return_value="pair")
            as normalize,
            patch("core.satellite.spacenet_adapter.classify_normalized_pair", return_value=expected)
            as classify,
        ):
            result = classify_spacenet_pair("pre.tif", "post.tif", 4, 0.8)
        self.assertIs(result, expected)
        normalize.assert_called_once_with("pre.tif", "post.tif", minimum_valid_fraction=0.0)
        classify.assert_called_once_with("pair", 4, 0.8)

    def test_generic_classifier_still_rejects_mismatched_sizes(self):
        with patch("core.satellite.broad_area_classifier.Image.open", side_effect=[
            Image.new("RGB", (10, 10)), Image.new("RGB", (8, 8)),
        ]):
            with self.assertRaisesRegex(ValueError, "sizes differ"):
                classify_area("pre", "post", (-90, 29, -89, 30), 2)

if __name__ == "__main__":
    unittest.main()
