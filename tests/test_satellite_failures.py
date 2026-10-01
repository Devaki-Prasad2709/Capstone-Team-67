"""Fail-closed behavior for the universal satellite normalization boundary."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.enums import ColorInterp
from rasterio.errors import RasterioError
from rasterio.transform import Affine, from_origin

from core.satellite.normalizer import (
    AffineTransform,
    IncompatibleBandsError,
    IncompatibleSensorError,
    InsufficientOverlapError,
    InsufficientValidCoverageError,
    InvalidOutputGridError,
    MissingGeoreferencingError,
    NoGeographicOverlapError,
    RasterProvenance,
    RasterReadError,
    ReprojectionError,
    SelectedBand,
    UnsupportedTransformError,
    _validate_source_georeferencing,
    create_normalized_pair,
    normalize_satellite_pair,
)


def _write_raster(
    path,
    values=None,
    *,
    transform=None,
    crs="EPSG:4326",
    nodata=None,
    colorinterp=None,
    sensor_type="optical",
):
    data = np.asarray(
        np.full((4, 4), 100, dtype=np.uint8) if values is None else values
    )
    if data.ndim == 2:
        data = data[np.newaxis, :, :]
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=data.shape[2],
        height=data.shape[1],
        count=data.shape[0],
        dtype=data.dtype,
        crs=crs,
        transform=transform or from_origin(0, 4, 1, 1),
        nodata=nodata,
    ) as destination:
        destination.write(data)
        if colorinterp is not None:
            destination.colorinterp = tuple(colorinterp)
        if sensor_type is not None:
            destination.update_tags(SENSOR_TYPE=sensor_type)


def _assert_descriptive(error, role):
    assert error.value.input_role == role
    assert error.value.detail
    assert error.value.normalization_requirement
    assert f"[{role}]" in str(error.value)
    assert "Requirement:" in str(error.value)


@pytest.mark.parametrize("missing_role", ["PRE", "POST"])
def test_missing_input_file_identifies_the_failed_source(tmp_path, missing_role):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    if missing_role != "PRE":
        _write_raster(pre)
    if missing_role != "POST":
        _write_raster(post)

    with pytest.raises(RasterReadError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, missing_role)
    assert "Could not read raster" in error.value.detail


def test_corrupt_raster_is_rejected_as_unreadable_pre_input(tmp_path):
    pre, post = tmp_path / "corrupt.tif", tmp_path / "post.tif"
    pre.write_bytes(b"not a raster")
    _write_raster(post)

    with pytest.raises(RasterReadError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, "PRE")
    assert str(pre) in error.value.detail


def test_missing_crs_identifies_pre_georeferencing_requirement(tmp_path):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(pre, crs=None)
    _write_raster(post)

    with pytest.raises(MissingGeoreferencingError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, "PRE")
    assert "no CRS" in error.value.detail


def test_unsupported_source_transform_identifies_pre_input():
    source = SimpleNamespace(
        crs="EPSG:4326",
        transform=Affine(float("nan"), 0, 0, 0, -1, 4),
        width=4,
        height=4,
        count=1,
        bounds=(0, 0, 4, 4),
        name="unsafe-pre.tif",
    )
    with pytest.raises(UnsupportedTransformError) as error:
        _validate_source_georeferencing(source, "PRE")
    _assert_descriptive(error, "PRE")
    assert "non-finite" in error.value.detail


def test_no_geographic_overlap_rejects_the_pair(tmp_path):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(pre, transform=from_origin(0, 4, 1, 1))
    _write_raster(post, transform=from_origin(10, 14, 1, 1))

    with pytest.raises(NoGeographicOverlapError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, "PAIR")
    assert "PRE bounds" in error.value.detail and "POST bounds" in error.value.detail


def test_subpixel_overlap_is_rejected_instead_of_extrapolated(tmp_path):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(pre, transform=from_origin(0, 4, 1, 1))
    _write_raster(post, transform=from_origin(3.75, 4, 1, 1))

    with pytest.raises(InsufficientOverlapError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, "PAIR")
    assert "smaller than one pixel" in error.value.detail


def test_insufficient_valid_pixels_rejects_pair_with_coverage_reason(tmp_path):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    values = np.zeros((4, 4), dtype=np.uint8)
    values[0, 0] = 100
    _write_raster(pre, values, nodata=0)
    _write_raster(post)

    with pytest.raises(InsufficientValidCoverageError) as error:
        normalize_satellite_pair(pre, post, minimum_valid_fraction=0.5)
    _assert_descriptive(error, "PAIR")
    assert "below the required" in error.value.detail


def test_missing_required_band_semantics_rejects_pair(tmp_path):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    values = np.ones((2, 4, 4), dtype=np.uint8)
    incomplete = (ColorInterp.red, ColorInterp.green)
    _write_raster(pre, values, colorinterp=incomplete)
    _write_raster(post, values, colorinterp=incomplete)

    with pytest.raises(IncompatibleBandsError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, "PAIR")
    assert "matching RGB or grayscale" in error.value.detail


def test_unsupported_sensor_representation_rejects_pair(tmp_path):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(pre, sensor_type="sar")
    _write_raster(post, sensor_type="sar")

    with pytest.raises(IncompatibleSensorError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, "PAIR")
    assert "supports optical intensity" in error.value.detail


def test_reprojection_failure_is_translated_without_fabricating_output(tmp_path, monkeypatch):
    pre, post = tmp_path / "pre.tif", tmp_path / "post.tif"
    _write_raster(pre, transform=from_origin(0, 4, 1, 1))
    _write_raster(post, transform=from_origin(0.5, 3.5, 1, 1))

    def fail_reprojection(*args, **kwargs):
        raise RasterioError("forced reprojection failure")

    monkeypatch.setattr("core.satellite.normalizer.reproject", fail_reprojection)
    with pytest.raises(ReprojectionError) as error:
        normalize_satellite_pair(pre, post)
    _assert_descriptive(error, "PAIR")
    assert "forced reprojection failure" in error.value.detail


def test_invalid_output_bounds_are_rejected_as_output_contract_failure():
    values = np.ones((2, 2), dtype=np.uint8)
    provenance = RasterProvenance("test")
    with pytest.raises(InvalidOutputGridError) as error:
        create_normalized_pair(
            pre=values,
            post=values,
            valid_mask=np.ones((2, 2), dtype=bool),
            working_crs="EPSG:4326",
            transform=AffineTransform(1, 0, 0, 0, -1, 2),
            bounds=(0, 0, 3, 2),
            width=2,
            height=2,
            selected_bands=(SelectedBand("luminance", 1, 1),),
            output_bands=("luminance",),
            representation="grayscale_uint8_0_255",
            pre_provenance=provenance,
            post_provenance=provenance,
        )
    _assert_descriptive(error, "OUTPUT")
    assert "do not match" in error.value.detail


def test_invalid_output_transform_identifies_output_contract_failure():
    with pytest.raises(UnsupportedTransformError) as error:
        AffineTransform(1, 2, 0, 2, 4, 0)
    _assert_descriptive(error, "OUTPUT")
    assert "singular" in error.value.detail
