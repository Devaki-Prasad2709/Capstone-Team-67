"""Real-data regression checks for SpaceNet through the universal adapter.

These tests deliberately skip when the external SpaceNet dataset is not
mounted. A skip is not evidence that the real-data integration passed.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pytest
import rasterio
from pyproj import Transformer
from shapely.geometry import Polygon, shape

from config.settings import settings
from core.satellite.broad_area_classifier import classify_normalized_pair
from core.satellite.normalizer import normalize_satellite_pair


SCENARIO_MANIFEST = (
    settings.project_root / "scenarios" / "louisiana_east_flood" / "satellite" / "pair.json"
)
ACTIVE_MANIFEST = json.loads(SCENARIO_MANIFEST.read_text(encoding="utf-8"))


def _dataset_root() -> Path | None:
    configured = (
        os.getenv("SPACENET8_TEST_ROOT")
        or os.getenv("SPACENET8_DATASET_PATH")
        or settings.spacenet8_dataset_path
    )
    return Path(configured).expanduser() if configured else None


DATASET_ROOT = _dataset_root()
REAL_DATA_AVAILABLE = DATASET_ROOT is not None and DATASET_ROOT.is_dir()
REAL_DATA_REASON = (
    f"SpaceNet dataset is unavailable at configured root {DATASET_ROOT}"
    if DATASET_ROOT
    else "Set SPACENET8_TEST_ROOT or SPACENET8_DATASET_PATH for real-data tests"
)

CASES = (
    {
        "id": "contract-0_10_2",
        "tile_id": "0_10_2",
        "pre": "PRE-event/105001001A0FFC00_0_10_2.tif",
        "post": "POST-event/10300100C46F5900_0_10_2.tif",
        "pre_size": (1300, 1300),
        "post_size": (1114, 1114),
        "grid_size": 4,
    },
    {
        "id": "active-2_23_44",
        "tile_id": "2_23_44",
        "pre": ACTIVE_MANIFEST["pre_event"]["relative_path"],
        "post": ACTIVE_MANIFEST["post_event"]["relative_path"],
        "pre_size": (
            ACTIVE_MANIFEST["pre_event"]["width"],
            ACTIVE_MANIFEST["pre_event"]["height"],
        ),
        "post_size": (
            ACTIVE_MANIFEST["post_event"]["width"],
            ACTIVE_MANIFEST["post_event"]["height"],
        ),
        "grid_size": 8,
    },
)


def _wgs84_footprint(path: Path) -> Polygon:
    with rasterio.open(path) as source:
        transform = source.transform
        corners = (
            (0, 0),
            (source.width, 0),
            (source.width, source.height),
            (0, source.height),
        )
        native = [
            (
                transform.a * column + transform.b * row + transform.c,
                transform.d * column + transform.e * row + transform.f,
            )
            for column, row in corners
        ]
        converter = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True)
        return Polygon([converter.transform(x, y) for x, y in native])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@pytest.mark.skipif(not REAL_DATA_AVAILABLE, reason=REAL_DATA_REASON)
@pytest.mark.parametrize("case", CASES, ids=lambda case: case["id"])
def test_spacenet_pair_uses_universal_geographic_normalization(case):
    assert DATASET_ROOT is not None
    pre_path = DATASET_ROOT / case["pre"]
    post_path = DATASET_ROOT / case["post"]
    assert pre_path.is_file(), f"PRE input is missing: {pre_path}"
    assert post_path.is_file(), f"POST input is missing: {post_path}"

    with rasterio.open(pre_path) as pre_source, rasterio.open(post_path) as post_source:
        assert (pre_source.width, pre_source.height) == case["pre_size"]
        assert (post_source.width, post_source.height) == case["post_size"]
        # Both regression fixtures exercise geographic alignment rather than a
        # byte-for-byte compatible-grid shortcut.
        assert (
            pre_source.width,
            pre_source.height,
            pre_source.transform,
        ) != (
            post_source.width,
            post_source.height,
            post_source.transform,
        )

    pair = normalize_satellite_pair(pre_path, post_path)

    assert pair.pre.shape == pair.post.shape == pair.valid_mask.shape
    assert pair.pre.shape == (pair.height, pair.width)
    assert pair.working_crs
    assert pair.bounds == pair.transform.grid_bounds(pair.width, pair.height)
    assert pair.valid_mask.dtype == np.bool_
    assert pair.valid_mask.any()
    assert 0.0 < pair.valid_fraction <= 1.0
    assert pair.pre_provenance.source_path == str(pre_path)
    assert pair.post_provenance.source_path == str(post_path)
    assert (
        pair.pre_provenance.metadata["transform"]
        != pair.post_provenance.metadata["transform"]
    )
    assert (
        pair.pre_provenance.metadata["normalization_path"]
        == "reprojected_to_common_grid"
    )
    assert (
        pair.post_provenance.metadata["normalization_path"]
        == "reprojected_to_common_grid"
    )

    result = classify_normalized_pair(pair, grid_size=case["grid_size"])
    assert result["type"] == "FeatureCollection"
    assert result["metadata"]["crs"] == "EPSG:4326"
    assert result["metadata"]["working_crs"] == pair.working_crs
    assert result["metadata"]["working_transform"] == pair.transform.as_tuple()
    assert result["metadata"]["working_bounds"] == pair.bounds
    assert result["metadata"]["normalized_size"] == [pair.width, pair.height]
    assert result["metadata"]["pre_provenance"]["source_path"] == str(pre_path)
    assert result["metadata"]["post_provenance"]["source_path"] == str(post_path)

    true_shared_footprint = _wgs84_footprint(pre_path).intersection(
        _wgs84_footprint(post_path)
    )
    assert not true_shared_footprint.is_empty
    cells = [shape(feature["geometry"]) for feature in result["features"]]
    assert len(cells) == case["grid_size"] ** 2
    assert all(cell.is_valid for cell in cells)
    # Tiny tolerance accommodates floating-point CRS round trips only; it
    # cannot conceal a pixel-scale extrapolation beyond either source image.
    shared_with_tolerance = true_shared_footprint.buffer(1e-10)
    assert all(shared_with_tolerance.covers(cell) for cell in cells)

    scores = [
        feature["properties"]["diff_score"]
        for feature in result["features"]
        if feature["properties"]["diff_score"] is not None
    ]
    assert scores, "all change cells were unknown despite overlapping valid imagery"
    assert all(math.isfinite(score) and 0.0 <= score <= 1.0 for score in scores)
    for feature in result["features"]:
        properties = feature["properties"]
        if properties["diff_score"] is None:
            assert properties["severity"] == "unknown"
            assert properties["valid_fraction"] < 0.95

    if case["tile_id"] == ACTIVE_MANIFEST["tile_id"]:
        assert case["tile_id"] == "2_23_44"
        assert _sha256(pre_path) == ACTIVE_MANIFEST["pre_event"]["sha256"]
        assert _sha256(post_path) == ACTIVE_MANIFEST["post_event"]["sha256"]
        assert ACTIVE_MANIFEST["provenance"]["imagery_and_labels"] == "real"
        assert ACTIVE_MANIFEST["provenance"]["scenario_time"].startswith("simulated")
