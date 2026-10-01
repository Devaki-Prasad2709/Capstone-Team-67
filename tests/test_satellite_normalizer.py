"""Contract tests for the universal satellite normalization boundary."""

from __future__ import annotations

import inspect

import numpy as np
import pytest
import rasterio
from rasterio.enums import ColorInterp
from rasterio.transform import from_bounds, from_origin

from core.satellite.broad_area_classifier import classify_normalized_pair
from core.satellite.normalizer import (
    AffineTransform,
    IncompatibleBandsError,
    IncompatibleRasterRepresentationError,
    IncompatibleSensorError,
    InsufficientValidCoverageError,
    InvalidOutputGridError,
    MissingGeoreferencingError,
    NoGeographicOverlapError,
    RasterProvenance,
    RasterReadError,
    SatelliteNormalizationError,
    SelectedBand,
    UnsupportedCRSError,
    UnsupportedTransformError,
    create_normalized_pair,
    geographic_intersection,
    normalize_satellite_pair,
)
from core.satellite.spacenet_adapter import (
    classify_spacenet_pair,
    classify_transferred_pair,
)


def _pair(**overrides):
    values = {
        "pre": np.arange(12, dtype=np.float32).reshape(3, 4),
        "post": np.arange(12, dtype=np.float32).reshape(3, 4) + 1,
        "valid_mask": np.ones((3, 4), dtype=bool),
        "working_crs": "EPSG:32615",
        "transform": AffineTransform(10, 0, 500_000, 0, -10, 3_300_000),
        "bounds": (500_000, 3_299_970, 500_040, 3_300_000),
        "width": 4,
        "height": 3,
        "selected_bands": (SelectedBand("luminance", 1, 1),),
        "representation": "normalized_intensity",
        "pre_provenance": RasterProvenance(
            "pre-scene", source_path="pre.tif", driver="GTiff", original_crs="EPSG:4326"
        ),
        "post_provenance": RasterProvenance(
            "post-scene", source_path="post.tif", driver="GTiff", original_crs="EPSG:4326"
        ),
    }
    values.update(overrides)
    return create_normalized_pair(**values)


def test_normalized_pair_exposes_complete_contract():
    pair = _pair(valid_mask=np.array(
        [[True, True, False, True], [True, True, True, True], [True, True, True, True]],
        dtype=bool,
    ))

    assert pair.pre.shape == pair.post.shape == (pair.height, pair.width)
    assert pair.valid_mask.shape == (pair.height, pair.width)
    assert pair.working_crs == "EPSG:32615"
    assert pair.transform.as_tuple() == (10, 0, 500_000, 0, -10, 3_300_000)
    assert pair.bounds == (500_000.0, 3_299_970.0, 500_040.0, 3_300_000.0)
    assert pair.band_count == 1
    assert pair.selected_bands[0].semantic == "luminance"
    assert pair.pre_provenance.source_path == "pre.tif"
    assert pair.post_provenance.source_path == "post.tif"
    assert pair.valid_fraction == pytest.approx(11 / 12)


def test_channel_count_requires_explicit_selected_band_mapping():
    rgb = np.ones((3, 4, 3), dtype=np.uint8)
    with pytest.raises(IncompatibleBandsError, match="3 normalized channel"):
        _pair(pre=rgb, post=rgb.copy())

    pair = _pair(
        pre=rgb,
        post=rgb.copy(),
        selected_bands=(
            SelectedBand("red", 1, 1),
            SelectedBand("green", 2, 2),
            SelectedBand("blue", 3, 3),
        ),
        representation="optical_rgb_uint8",
    )
    assert pair.band_count == 3

    with pytest.raises(IncompatibleBandsError, match="positive integers"):
        SelectedBand("red", 1.5, 1)


def test_missing_or_invalid_georeferencing_fails_with_domain_errors():
    with pytest.raises(MissingGeoreferencingError, match="working CRS"):
        _pair(working_crs="")
    with pytest.raises(UnsupportedCRSError, match="Unsupported working CRS"):
        _pair(working_crs="definitely-not-a-crs")
    with pytest.raises(UnsupportedTransformError, match="singular"):
        AffineTransform(1, 2, 0, 2, 4, 0)
    with pytest.raises(InvalidOutputGridError, match="do not match"):
        _pair(bounds=(0, 0, 1, 1))
    with pytest.raises(InvalidOutputGridError, match="positive integers"):
        _pair(width=4.5)


def test_geographic_overlap_is_explicit_and_never_extrapolated():
    assert geographic_intersection((0, 0, 10, 10), (5, -2, 12, 8)) == (5, 0, 10, 8)
    with pytest.raises(NoGeographicOverlapError, match="no geographic overlap"):
        geographic_intersection((0, 0, 1, 1), (2, 2, 3, 3))


def test_valid_mask_shape_type_and_minimum_coverage_are_enforced():
    with pytest.raises(InsufficientValidCoverageError, match="boolean array"):
        _pair(valid_mask=np.ones((3, 4), dtype=np.uint8))
    with pytest.raises(InsufficientValidCoverageError, match="shape"):
        _pair(valid_mask=np.ones((2, 4), dtype=bool))
    with pytest.raises(InsufficientValidCoverageError, match="below the required"):
        _pair(
            valid_mask=np.array(
                [[True, False, False, False], [True, False, False, False], [False] * 4],
                dtype=bool,
            ),
            minimum_valid_fraction=0.5,
        )


def test_nonfinite_valid_pixels_and_incompatible_shapes_are_rejected():
    post = np.arange(12, dtype=np.float32).reshape(3, 4)
    post[0, 0] = np.nan
    with pytest.raises(IncompatibleRasterRepresentationError, match="finite"):
        _pair(post=post)
    with pytest.raises(IncompatibleBandsError, match="shapes differ"):
        _pair(post=np.ones((2, 4), dtype=np.float32))


def test_provenance_is_copied_and_domain_errors_share_one_base_type():
    metadata = {"dataset": "example", "unused_source_field": 42}
    provenance = RasterProvenance("pre", metadata=metadata)
    metadata["dataset"] = "mutated"
    assert provenance.metadata["dataset"] == "example"
    assert issubclass(RasterReadError, SatelliteNormalizationError)
    assert issubclass(IncompatibleBandsError, SatelliteNormalizationError)


def test_existing_spacenet_public_entry_points_remain_compatible():
    direct = inspect.signature(classify_spacenet_pair)
    transported = inspect.signature(classify_transferred_pair)
    assert list(direct.parameters) == [
        "pre_image_path", "post_image_path", "grid_size", "min_valid_fraction"
    ]
    assert list(transported.parameters) == [
        "pre_image_bytes", "post_image_bytes", "pre_bbox", "post_bbox",
        "grid_size", "min_valid_fraction",
    ]


def _write_raster(
    path,
    values,
    *,
    crs="EPSG:4326",
    transform=None,
    nodata=None,
    colorinterp=None,
    sensor_type="optical",
):
    data = np.asarray(values)
    if data.ndim == 2:
        data = data[np.newaxis, :, :]
    if transform is None:
        transform = from_origin(0, data.shape[1], 1, 1)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[2],
        height=data.shape[1],
        count=data.shape[0],
        dtype=data.dtype,
        crs=crs,
        transform=transform,
        nodata=nodata,
    ) as destination:
        destination.write(data)
        if colorinterp is not None:
            destination.colorinterp = tuple(colorinterp)
        if sensor_type is not None:
            destination.update_tags(SENSOR_TYPE=sensor_type)


def test_compatible_pair_uses_fast_path_without_reprojection(tmp_path, monkeypatch):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    transform = from_origin(-90.1, 29.8, 0.01, 0.01)
    pre = np.arange(16, dtype=np.uint8).reshape(4, 4)
    post = pre + 2
    post[0, 0] = 0
    _write_raster(pre_path, pre, transform=transform, nodata=0)
    _write_raster(post_path, post, transform=transform, nodata=0)

    def unexpected_reprojection(*args, **kwargs):
        raise AssertionError("compatible rasters must not be reprojected")

    monkeypatch.setattr("core.satellite.normalizer.reproject", unexpected_reprojection)
    pair = normalize_satellite_pair(pre_path, post_path)

    np.testing.assert_array_equal(pair.pre, pre)
    np.testing.assert_array_equal(pair.post, post)
    assert pair.working_crs == "EPSG:4326"
    assert pair.bounds == pytest.approx((-90.1, 29.76, -90.06, 29.8))
    assert pair.pre_provenance.metadata["normalization_path"] == "compatible_fast_path"
    assert not pair.valid_mask[0, 0]


def test_different_dimensions_and_pixel_sizes_use_coarser_common_grid(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(
        pre_path,
        np.full((4, 4), 10, dtype=np.uint8),
        transform=from_origin(0, 4, 1, 1),
    )
    _write_raster(
        post_path,
        np.full((8, 8), 20, dtype=np.uint8),
        transform=from_origin(0, 4, 0.5, 0.5),
    )

    pair = normalize_satellite_pair(pre_path, post_path)

    assert (pair.height, pair.width) == (4, 4)
    assert pair.transform.as_tuple() == pytest.approx((1, 0, 0, 0, -1, 4))
    assert pair.bounds == pytest.approx((0, 0, 4, 4))
    assert pair.valid_mask.all()
    assert pair.pre_provenance.metadata["normalization_path"] == "reprojected_to_common_grid"


def test_different_grid_origins_are_aligned_by_geography(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(
        pre_path,
        np.full((4, 4), 10, dtype=np.uint8),
        transform=from_origin(0, 4, 1, 1),
    )
    _write_raster(
        post_path,
        np.full((4, 4), 20, dtype=np.uint8),
        transform=from_origin(0.5, 3.5, 1, 1),
    )

    pair = normalize_satellite_pair(pre_path, post_path)

    # The half-pixel borders are clipped inward rather than extrapolated.
    assert (pair.height, pair.width) == (3, 3)
    assert pair.bounds == pytest.approx((0.5, 0.5, 3.5, 3.5))
    assert pair.valid_mask.all()


def test_partial_extent_is_cropped_to_intersection_without_extrapolation(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(
        pre_path,
        np.full((4, 4), 10, dtype=np.uint8),
        transform=from_origin(0, 4, 1, 1),
    )
    _write_raster(
        post_path,
        np.full((4, 4), 20, dtype=np.uint8),
        transform=from_origin(2, 4, 1, 1),
    )

    pair = normalize_satellite_pair(pre_path, post_path)

    assert (pair.height, pair.width) == (4, 2)
    assert pair.bounds == pytest.approx((2, 0, 4, 4))
    assert pair.valid_mask.all()


def test_different_crs_reprojects_to_existing_projected_crs(tmp_path):
    from pyproj import Transformer

    pre_path, post_path = tmp_path / "pre-wgs84.tif", tmp_path / "post-webmercator.tif"
    wgs84_bounds = (-90.1, 29.7, -90.0, 29.8)
    _write_raster(
        pre_path,
        np.full((10, 10), 10, dtype=np.uint8),
        crs="EPSG:4326",
        transform=from_bounds(*wgs84_bounds, 10, 10),
    )
    transformer = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    west, south = transformer.transform(wgs84_bounds[0], wgs84_bounds[1])
    east, north = transformer.transform(wgs84_bounds[2], wgs84_bounds[3])
    _write_raster(
        post_path,
        np.full((12, 12), 20, dtype=np.uint8),
        crs="EPSG:3857",
        transform=from_bounds(west, south, east, north, 12, 12),
    )

    pair = normalize_satellite_pair(pre_path, post_path)

    assert pair.working_crs == "EPSG:3857"
    assert pair.width > 0 and pair.height > 0
    assert pair.valid_fraction > 0.8
    assert west <= pair.bounds[0] < pair.bounds[2] <= east
    assert south <= pair.bounds[1] < pair.bounds[3] <= north


def test_nonoverlapping_rasters_fail_safely(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(pre_path, np.ones((2, 2), dtype=np.uint8), transform=from_origin(0, 2, 1, 1))
    _write_raster(post_path, np.ones((2, 2), dtype=np.uint8), transform=from_origin(5, 7, 1, 1))

    with pytest.raises(NoGeographicOverlapError, match="no geographic overlap"):
        normalize_satellite_pair(pre_path, post_path)


def test_missing_georeferencing_is_rejected(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    data = np.ones((2, 2), dtype=np.uint8)
    _write_raster(pre_path, data, crs=None, transform=rasterio.Affine.identity())
    _write_raster(post_path, data, transform=from_origin(0, 2, 1, 1))

    with pytest.raises(MissingGeoreferencingError, match=r"\[PRE\].*no CRS"):
        normalize_satellite_pair(pre_path, post_path)


def test_rgb_selection_discards_extra_unused_band_and_matches_grayscale_policy(tmp_path):
    pre_path, post_path = tmp_path / "pre-rgba.tif", tmp_path / "post-rgba.tif"
    pre = np.stack([
        np.full((2, 2), 255, dtype=np.uint8),
        np.zeros((2, 2), dtype=np.uint8),
        np.zeros((2, 2), dtype=np.uint8),
        np.full((2, 2), 17, dtype=np.uint8),
    ])
    post = pre.copy()
    post[3] = 240  # The unused alpha/extra band must not affect the result.
    interpretations = (ColorInterp.red, ColorInterp.green, ColorInterp.blue, ColorInterp.alpha)
    _write_raster(pre_path, pre, colorinterp=interpretations)
    _write_raster(post_path, post, colorinterp=interpretations)

    pair = normalize_satellite_pair(pre_path, post_path)

    assert pair.pre.shape == pair.post.shape == (2, 2)
    assert pair.pre.dtype == pair.post.dtype == np.uint8
    np.testing.assert_array_equal(pair.pre, np.full((2, 2), 76, dtype=np.uint8))
    np.testing.assert_array_equal(pair.post, pair.pre)
    assert [band.semantic for band in pair.selected_bands] == ["red", "green", "blue"]
    assert pair.output_bands == ("luminance",)
    assert pair.representation == "grayscale_uint8_0_255"


def test_integer_dtype_differences_share_classifier_numeric_domain(tmp_path):
    pre_path, post_path = tmp_path / "pre-u8.tif", tmp_path / "post-u16.tif"
    _write_raster(pre_path, np.full((2, 2), 128, dtype=np.uint8))
    _write_raster(post_path, np.full((2, 2), 32896, dtype=np.uint16))

    pair = normalize_satellite_pair(pre_path, post_path)

    np.testing.assert_array_equal(pair.pre, np.full((2, 2), 128, dtype=np.uint8))
    np.testing.assert_array_equal(pair.post, np.full((2, 2), 128, dtype=np.uint8))


def test_declared_nodata_from_either_image_is_never_a_valid_zero_observation(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    pre = np.full((3, 3), 100, dtype=np.uint8)
    post = np.full((3, 3), 100, dtype=np.uint8)
    pre[0, 0] = 0
    post[2, 2] = 255
    _write_raster(pre_path, pre, nodata=0)
    _write_raster(post_path, post, nodata=255)

    pair = normalize_satellite_pair(pre_path, post_path)

    assert pair.valid_fraction == pytest.approx(7 / 9)
    assert not pair.valid_mask[0, 0]
    assert not pair.valid_mask[2, 2]
    assert pair.pre[0, 0] == 0  # storage value only; mask makes it unknown
    assert pair.post[2, 2] == 0


def test_resampling_does_not_validate_pixels_with_nodata_interpolation_support(tmp_path):
    pre_path, post_path = tmp_path / "pre-shifted.tif", tmp_path / "post-shifted.tif"
    pre = np.full((4, 4), 100, dtype=np.uint8)
    pre[1, 1] = 0
    _write_raster(pre_path, pre, nodata=0, transform=from_origin(0, 4, 1, 1))
    _write_raster(
        post_path,
        np.full((4, 4), 100, dtype=np.uint8),
        transform=from_origin(0.5, 3.5, 1, 1),
    )

    pair = normalize_satellite_pair(pre_path, post_path)

    assert pair.valid_mask.any()
    assert not pair.valid_mask.all()
    assert np.all(pair.pre[~pair.valid_mask] == 0)


def test_partial_valid_coverage_is_preserved_or_rejected_by_threshold(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    pre = np.full((4, 4), 100, dtype=np.uint8)
    pre[0, :] = 0
    _write_raster(pre_path, pre, nodata=0)
    _write_raster(post_path, np.full((4, 4), 100, dtype=np.uint8))

    pair = normalize_satellite_pair(pre_path, post_path, minimum_valid_fraction=0.7)
    assert pair.valid_fraction == pytest.approx(0.75)
    assert not pair.valid_mask[0].any()

    with pytest.raises(InsufficientValidCoverageError, match="below the required"):
        normalize_satellite_pair(pre_path, post_path, minimum_valid_fraction=0.8)


def test_missing_required_color_semantics_fail_clearly(tmp_path):
    pre_path, post_path = tmp_path / "pre-two-band.tif", tmp_path / "post-two-band.tif"
    values = np.ones((2, 2, 2), dtype=np.uint8)
    incomplete_rgb = (ColorInterp.red, ColorInterp.green)
    _write_raster(pre_path, values, colorinterp=incomplete_rgb)
    _write_raster(post_path, values, colorinterp=incomplete_rgb)

    with pytest.raises(IncompatibleBandsError, match="requires matching RGB or grayscale"):
        normalize_satellite_pair(pre_path, post_path)


def test_incompatible_sensor_representations_are_not_silently_interpreted(tmp_path):
    pre_path, post_path = tmp_path / "pre-optical.tif", tmp_path / "post-sar.tif"
    values = np.ones((2, 2), dtype=np.uint8)
    _write_raster(pre_path, values, sensor_type="optical")
    _write_raster(post_path, values, sensor_type="sar")

    with pytest.raises(IncompatibleSensorError, match="not interchangeable"):
        normalize_satellite_pair(pre_path, post_path)


def test_float_bands_require_explicit_common_numeric_range(tmp_path):
    pre_path, post_path = tmp_path / "pre-float.tif", tmp_path / "post-float.tif"
    _write_raster(pre_path, np.full((2, 2), 0.5, dtype=np.float32))
    _write_raster(post_path, np.full((2, 2), 0.5, dtype=np.float32))

    with pytest.raises(IncompatibleRasterRepresentationError, match="numeric_range"):
        normalize_satellite_pair(pre_path, post_path)

    pair = normalize_satellite_pair(pre_path, post_path, numeric_range=(0.0, 1.0))
    np.testing.assert_array_equal(pair.pre, np.full((2, 2), 128, dtype=np.uint8))
    np.testing.assert_array_equal(pair.post, pair.pre)


def test_generic_classifier_consumes_normalized_pair_and_emits_wgs84_unknown_cells(tmp_path):
    pre_path, post_path = tmp_path / "pre.tif", tmp_path / "post.tif"
    transform = from_origin(-90.1, 29.8, 0.01, 0.01)
    pre = np.full((4, 4), 100, dtype=np.uint8)
    post = np.full((4, 4), 200, dtype=np.uint8)
    pre[0, 0] = 0
    _write_raster(pre_path, pre, transform=transform, nodata=0)
    _write_raster(post_path, post, transform=transform)

    pair = normalize_satellite_pair(pre_path, post_path)
    result = classify_normalized_pair(pair, grid_size=2, min_valid_fraction=0.8)

    assert result["type"] == "FeatureCollection"
    assert result["metadata"]["source"] == "universal_satellite_normalizer"
    assert result["metadata"]["crs"] == "EPSG:4326"
    assert result["metadata"]["pre_provenance"]["source_path"].endswith("pre.tif")
    assert len(result["features"]) == 4
    first = result["features"][0]
    assert first["properties"]["diff_score"] is None
    assert first["properties"]["severity"] == "unknown"
    assert first["properties"]["quality"] == "insufficient_coverage"
    assert result["features"][1]["properties"]["diff_score"] == round(100 / 255, 4)
    coordinates = [value for point in first["geometry"]["coordinates"][0] for value in point]
    assert all(np.isfinite(coordinates))
    assert min(coordinates[::2]) >= -180 and max(coordinates[::2]) <= 180
    assert min(coordinates[1::2]) >= -90 and max(coordinates[1::2]) <= 90
