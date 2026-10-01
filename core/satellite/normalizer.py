"""Shared input/output contract for satellite PRE/POST normalization.

This module deliberately contains no raster reader, reprojection, resampling,
change scoring, or dashboard logic.  It defines the boundary that a universal
normalizer must satisfy before the generic broad-area classifier is called.

Arrays use ``(height, width)`` for one selected band and
``(height, width, bands)`` for multiple selected bands.  ``bounds`` are in the
``working_crs`` and follow ``(west, south, east, north)`` order.  The affine
transform maps pixel-corner coordinates to that working CRS.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import math
from pathlib import Path
from typing import Mapping

import numpy as np
from numpy.typing import NDArray
from pyproj import CRS
from pyproj.exceptions import CRSError
import rasterio
from rasterio.crs import CRS as RasterioCRS
from rasterio.enums import Resampling
from rasterio.errors import RasterioError, RasterioIOError
from rasterio.io import MemoryFile
from rasterio.transform import Affine, from_origin
from rasterio.warp import calculate_default_transform, reproject, transform_bounds


Bounds = tuple[float, float, float, float]


class SatelliteNormalizationError(ValueError):
    """Base class for a satellite pair that cannot be normalized safely."""

    requirement = "safe satellite normalization"

    def __init__(
        self,
        detail: str,
        *,
        input_role: str = "PAIR",
        requirement: str | None = None,
    ) -> None:
        self.input_role = input_role
        self.detail = detail
        self.normalization_requirement = requirement or self.requirement
        super().__init__(
            f"[{input_role}] {detail} | Requirement: {self.normalization_requirement}"
        )


class RasterReadError(SatelliteNormalizationError):
    """A source raster is missing, corrupt, or cannot be decoded."""

    requirement = "source raster must exist and be readable"


class MissingGeoreferencingError(SatelliteNormalizationError):
    """A source does not contain the CRS/transform information it requires."""

    requirement = "source must provide explicit CRS, transform, and bounds"


class UnsupportedCRSError(SatelliteNormalizationError):
    """A source CRS is present but cannot be interpreted safely."""

    requirement = "CRS must be supported and safely transformable"


class UnsupportedTransformError(SatelliteNormalizationError):
    """A raster transform is malformed, singular, or inconsistent with its grid."""

    requirement = "source transform must be finite, invertible, and geospatially safe"


class ReprojectionError(SatelliteNormalizationError):
    """A supported source could not be safely reprojected to the common grid."""

    requirement = "PRE and POST must reproject safely onto one common geographic grid"


class InvalidOutputGridError(SatelliteNormalizationError):
    """The proposed normalized output grid is internally inconsistent."""

    requirement = "normalized output transform, bounds, dimensions, and arrays must agree"


class NoGeographicOverlapError(SatelliteNormalizationError):
    """The PRE and POST geographic footprints do not intersect."""

    requirement = "PRE and POST must share a non-empty geographic intersection"


class InsufficientOverlapError(SatelliteNormalizationError):
    """Geographic overlap is too small for one safe comparison pixel."""

    requirement = "shared geographic extent must contain at least one common-grid pixel"


class InsufficientValidCoverageError(SatelliteNormalizationError):
    """Too little corresponding valid data remains after normalization."""

    requirement = "corresponding valid-pixel coverage must meet the configured minimum"


class IncompatibleRasterRepresentationError(SatelliteNormalizationError):
    """The two rasters do not share a supported, meaningful representation."""

    requirement = "PRE and POST must share one supported numeric representation"


class IncompatibleBandsError(IncompatibleRasterRepresentationError):
    """Required PRE/POST bands cannot be matched without inventing semantics."""

    requirement = "required optical bands must be explicitly and semantically compatible"


class IncompatibleSensorError(IncompatibleRasterRepresentationError):
    """The PRE/POST sensor representations require specialized processing."""

    requirement = "sensor representations must be compatible with optical change scoring"


@dataclass(frozen=True)
class AffineTransform:
    """Dependency-light six-parameter raster affine transform.

    ``x = a * col + b * row + c``
    ``y = d * col + e * row + f``

    Rotation/shear can be represented here.  A concrete normalizer may still
    reject transformations it cannot resample safely.
    """

    a: float
    b: float
    c: float
    d: float
    e: float
    f: float

    def __post_init__(self) -> None:
        values = self.as_tuple()
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values):
            raise UnsupportedTransformError(
                "Raster transform must contain six finite numbers", input_role="OUTPUT"
            )
        determinant = self.a * self.e - self.b * self.d
        if math.isclose(determinant, 0.0, abs_tol=1e-15):
            raise UnsupportedTransformError("Raster transform is singular", input_role="OUTPUT")

    def as_tuple(self) -> tuple[float, float, float, float, float, float]:
        return (self.a, self.b, self.c, self.d, self.e, self.f)

    def pixel_corner(self, col: float, row: float) -> tuple[float, float]:
        return (
            self.a * col + self.b * row + self.c,
            self.d * col + self.e * row + self.f,
        )

    def grid_bounds(self, width: int, height: int) -> Bounds:
        if (
            isinstance(width, bool)
            or isinstance(height, bool)
            or not isinstance(width, (int, np.integer))
            or not isinstance(height, (int, np.integer))
            or width <= 0
            or height <= 0
        ):
            raise InvalidOutputGridError(
                "Raster width and height must be positive integers", input_role="OUTPUT"
            )
        corners = (
            self.pixel_corner(0, 0),
            self.pixel_corner(width, 0),
            self.pixel_corner(0, height),
            self.pixel_corner(width, height),
        )
        xs, ys = zip(*corners)
        return (min(xs), min(ys), max(xs), max(ys))


@dataclass(frozen=True)
class SelectedBand:
    """An explicit semantic mapping between one PRE and POST source band.

    Band indexes are one-based to match common raster metadata conventions.
    ``semantic`` must be supplied by source metadata/configuration; it must not
    be inferred merely from the position or value distribution of a band.
    """

    semantic: str
    pre_index: int
    post_index: int
    representation: str = "intensity"

    def __post_init__(self) -> None:
        if not self.semantic or not self.semantic.strip():
            raise IncompatibleBandsError("Selected bands require a semantic name")
        if (
            isinstance(self.pre_index, bool)
            or isinstance(self.post_index, bool)
            or not isinstance(self.pre_index, (int, np.integer))
            or not isinstance(self.post_index, (int, np.integer))
            or self.pre_index < 1
            or self.post_index < 1
        ):
            raise IncompatibleBandsError("Selected band indexes must be positive integers")
        if not self.representation or not self.representation.strip():
            raise IncompatibleBandsError("Selected bands require a representation")


@dataclass(frozen=True)
class RasterProvenance:
    """Debugging metadata retained from one source without preserving everything."""

    source_id: str
    source_path: str | None = None
    driver: str | None = None
    original_crs: str | None = None
    original_bounds: Bounds | None = None
    original_shape: tuple[int, ...] | None = None
    original_dtype: str | None = None
    sensor: str | None = None
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.source_id or not self.source_id.strip():
            raise RasterReadError("Raster provenance requires a non-empty source_id")
        object.__setattr__(self, "metadata", dict(self.metadata))


@dataclass(frozen=True)
class NormalizedSatellitePair:
    """PRE/POST arrays on one valid, georeferenced comparison grid."""

    pre: NDArray[np.number]
    post: NDArray[np.number]
    valid_mask: NDArray[np.bool_]
    working_crs: str
    transform: AffineTransform
    bounds: Bounds
    width: int
    height: int
    selected_bands: tuple[SelectedBand, ...]
    output_bands: tuple[str, ...]
    representation: str
    pre_provenance: RasterProvenance
    post_provenance: RasterProvenance

    @property
    def valid_fraction(self) -> float:
        return float(self.valid_mask.mean())

    @property
    def band_count(self) -> int:
        return 1 if self.pre.ndim == 2 else int(self.pre.shape[2])


def geographic_intersection(pre_bounds: Bounds, post_bounds: Bounds) -> Bounds:
    """Return the intersection of two bounds expressed in the same CRS."""
    pre = _validated_bounds(pre_bounds, "PRE")
    post = _validated_bounds(post_bounds, "POST")
    overlap = (
        max(pre[0], post[0]),
        max(pre[1], post[1]),
        min(pre[2], post[2]),
        min(pre[3], post[3]),
    )
    if overlap[0] >= overlap[2] or overlap[1] >= overlap[3]:
        raise NoGeographicOverlapError(
            f"PRE bounds {pre} and POST bounds {post} have no geographic overlap",
            input_role="PAIR",
        )
    return overlap


def create_normalized_pair(
    *,
    pre: NDArray[np.number],
    post: NDArray[np.number],
    valid_mask: NDArray[np.bool_],
    working_crs: str,
    transform: AffineTransform,
    bounds: Bounds,
    width: int,
    height: int,
    selected_bands: tuple[SelectedBand, ...],
    output_bands: tuple[str, ...] | None = None,
    representation: str,
    pre_provenance: RasterProvenance,
    post_provenance: RasterProvenance,
    minimum_valid_fraction: float = 0.0,
) -> NormalizedSatellitePair:
    """Validate and construct the universal normalizer's output contract.

    Concrete raster readers/resamplers should call this only after placing both
    rasters on their common grid.  No alignment or score calculation occurs
    here, making accidental array resizing at this boundary impossible.
    """
    pre_array = np.asarray(pre)
    post_array = np.asarray(post)
    mask = np.asarray(valid_mask)

    if pre_array.ndim not in (2, 3) or post_array.ndim not in (2, 3):
        raise IncompatibleBandsError("Normalized rasters must be 2D or channel-last 3D arrays")
    if pre_array.shape != post_array.shape:
        raise IncompatibleBandsError(
            f"Normalized PRE/POST shapes differ: {pre_array.shape} vs {post_array.shape}"
        )
    if not np.issubdtype(pre_array.dtype, np.number) or not np.issubdtype(
        post_array.dtype, np.number
    ):
        raise IncompatibleRasterRepresentationError("Normalized raster arrays must be numeric")
    if (
        isinstance(width, bool)
        or isinstance(height, bool)
        or not isinstance(width, (int, np.integer))
        or not isinstance(height, (int, np.integer))
        or width <= 0
        or height <= 0
    ):
        raise InvalidOutputGridError(
            "Normalized width and height must be positive integers", input_role="OUTPUT"
        )
    if pre_array.shape[:2] != (height, width):
        raise InvalidOutputGridError(
            f"Raster shape {pre_array.shape[:2]} does not match declared grid {(height, width)}",
            input_role="OUTPUT",
        )
    if mask.dtype != np.bool_ or mask.shape != (height, width):
        raise InsufficientValidCoverageError(
            f"valid_mask must be a boolean array with shape {(height, width)}"
        )

    band_count = 1 if pre_array.ndim == 2 else pre_array.shape[2]
    bands = tuple(selected_bands)
    if not bands:
        raise IncompatibleBandsError("At least one selected source band is required")
    semantics = [band.semantic.strip().lower() for band in bands]
    if len(set(semantics)) != len(semantics):
        raise IncompatibleBandsError("Selected band semantics must be unique")
    normalized_output_bands = tuple(
        str(value).strip().lower()
        for value in (output_bands if output_bands is not None else semantics)
    )
    if (
        len(normalized_output_bands) != band_count
        or any(not value for value in normalized_output_bands)
        or len(set(normalized_output_bands)) != len(normalized_output_bands)
    ):
        raise IncompatibleBandsError(
            f"{band_count} normalized channel(s) require {band_count} unique output band name(s)"
        )
    if not representation or not representation.strip():
        raise IncompatibleRasterRepresentationError(
            "Normalized pair requires an explicit common representation"
        )

    if not working_crs or not str(working_crs).strip():
        raise MissingGeoreferencingError(
            "Normalized pair has no working CRS", input_role="OUTPUT"
        )
    try:
        CRS.from_user_input(working_crs)
    except (CRSError, TypeError, ValueError) as exc:
        raise UnsupportedCRSError(
            f"Unsupported working CRS: {working_crs!r}", input_role="OUTPUT"
        ) from exc

    try:
        normalized_bounds = _validated_bounds(bounds, "OUTPUT")
    except MissingGeoreferencingError as exc:
        raise InvalidOutputGridError(exc.detail, input_role="OUTPUT") from exc
    expected_bounds = transform.grid_bounds(width, height)
    tolerance = max(1e-9, max(abs(value) for value in expected_bounds) * 1e-10)
    if not all(
        math.isclose(actual, expected, rel_tol=1e-9, abs_tol=tolerance)
        for actual, expected in zip(normalized_bounds, expected_bounds)
    ):
        raise InvalidOutputGridError(
            f"Normalized bounds {normalized_bounds} do not match transform/grid bounds "
            f"{expected_bounds}",
            input_role="OUTPUT",
        )

    if not isinstance(minimum_valid_fraction, (int, float)) or not math.isfinite(
        minimum_valid_fraction
    ) or not 0 <= minimum_valid_fraction <= 1:
        raise InsufficientValidCoverageError(
            "minimum_valid_fraction must be within [0, 1]", input_role="PAIR"
        )
    valid_fraction = float(mask.mean())
    required = max(float(minimum_valid_fraction), np.finfo(float).eps)
    if valid_fraction < required:
        raise InsufficientValidCoverageError(
            f"Valid geographic coverage {valid_fraction:.2%} is below the required "
            f"{minimum_valid_fraction:.2%}",
            input_role="PAIR",
        )
    if not np.isfinite(pre_array[mask]).all() or not np.isfinite(post_array[mask]).all():
        raise IncompatibleRasterRepresentationError(
            "Valid normalized pixels must contain finite numeric values"
        )

    return NormalizedSatellitePair(
        pre=pre_array,
        post=post_array,
        valid_mask=mask,
        working_crs=str(working_crs),
        transform=transform,
        bounds=normalized_bounds,
        width=int(width),
        height=int(height),
        selected_bands=bands,
        output_bands=normalized_output_bands,
        representation=representation.strip(),
        pre_provenance=pre_provenance,
        post_provenance=post_provenance,
    )


def normalize_satellite_pair(
    pre_image_path: str | Path,
    post_image_path: str | Path,
    *,
    selected_bands: tuple[SelectedBand, ...] | None = None,
    pre_sensor_type: str | None = None,
    post_sensor_type: str | None = None,
    numeric_range: tuple[float, float] | None = None,
    minimum_valid_fraction: float = 0.0,
) -> NormalizedSatellitePair:
    """Geographically align two supported rasters onto their common grid.

    An already-compatible pair is read directly without reprojection.  For all
    other supported pairs, the safer projected source CRS is preferred, the
    geographic intersection is clipped inward, and the coarser source pixel
    size is used.  Choosing the coarser resolution avoids inventing spatial
    detail through upsampling.  Raster values use bilinear resampling; mask
    coverage is resampled conservatively so every contributing source pixel
    must be valid.

    This function only normalizes geometry and valid coverage.  It does not
    calculate change scores or infer spectral/sensor semantics.
    """
    pre_path, post_path = Path(pre_image_path), Path(post_image_path)
    try:
        pre_source = rasterio.open(pre_path)
    except (OSError, RasterioIOError) as exc:
        raise RasterReadError(
            f"Could not read raster {pre_path}: {exc}", input_role="PRE"
        ) from exc
    try:
        try:
            post_source = rasterio.open(post_path)
        except (OSError, RasterioIOError) as exc:
            raise RasterReadError(
                f"Could not read raster {post_path}: {exc}", input_role="POST"
            ) from exc
        try:
            with post_source:
                return _normalize_open_pair(
                    pre_source,
                    post_source,
                    selected_bands=selected_bands,
                    pre_sensor_type=pre_sensor_type,
                    post_sensor_type=post_sensor_type,
                    numeric_range=numeric_range,
                    minimum_valid_fraction=minimum_valid_fraction,
                )
        except SatelliteNormalizationError:
            raise
        except (CRSError, RasterioError, ValueError) as exc:
            raise ReprojectionError(
                f"Could not align PRE {pre_path} with POST {post_path}: {exc}",
                input_role="PAIR",
            ) from exc
    finally:
        pre_source.close()


def normalize_transferred_pair(
    pre_image_bytes: bytes,
    post_image_bytes: bytes,
    pre_bounds: Bounds,
    post_bounds: Bounds,
    *,
    selected_bands: tuple[SelectedBand, ...] | None = None,
    sensor_type: str = "optical",
    numeric_range: tuple[float, float] | None = None,
    minimum_valid_fraction: float = 0.0,
) -> NormalizedSatellitePair:
    """Normalize transported raster representations with explicit WGS84 bounds.

    The existing producer intentionally transports JPEG representations, whose
    GeoTIFF tags no longer exist.  This boundary restores only the explicit
    footprint supplied by the producer contract; it does not fabricate other
    source metadata.
    """
    with _transported_raster(pre_image_bytes, pre_bounds, "PRE") as pre_source:
        with _transported_raster(post_image_bytes, post_bounds, "POST") as post_source:
            return _normalize_open_pair(
                pre_source,
                post_source,
                selected_bands=selected_bands,
                pre_sensor_type=sensor_type,
                post_sensor_type=sensor_type,
                numeric_range=numeric_range,
                minimum_valid_fraction=minimum_valid_fraction,
            )


@contextmanager
def _transported_raster(image_bytes: bytes, bounds: Bounds, label: str):
    if not isinstance(image_bytes, bytes) or not image_bytes:
        raise RasterReadError(
            "Transported raster payload is empty", input_role=label
        )
    footprint = _validated_bounds(bounds, label)
    try:
        with MemoryFile(image_bytes) as encoded_memory:
            with encoded_memory.open() as encoded:
                if encoded.count < 1 or encoded.width < 1 or encoded.height < 1:
                    raise RasterReadError(
                        "Transported raster has no readable pixels", input_role=label
                    )
                data = encoded.read()
                colorinterp = encoded.colorinterp
                profile = {
                    "driver": "GTiff",
                    "width": encoded.width,
                    "height": encoded.height,
                    "count": encoded.count,
                    "dtype": data.dtype,
                    "crs": "EPSG:4326",
                    "transform": rasterio.transform.from_bounds(
                        *footprint, encoded.width, encoded.height
                    ),
                }
        with MemoryFile() as georeferenced_memory:
            with georeferenced_memory.open(**profile) as destination:
                destination.write(data)
                if len(colorinterp) == destination.count:
                    destination.colorinterp = colorinterp
                destination.update_tags(
                    SENSOR_TYPE="optical",
                    TRANSPORT_GEOREFERENCE="explicit_wgs84_bounds",
                )
            with georeferenced_memory.open() as source:
                yield source
    except SatelliteNormalizationError:
        raise
    except (OSError, RasterioError, ValueError) as exc:
        raise RasterReadError(
            f"Could not decode transported raster: {exc}", input_role=label
        ) from exc


def _normalize_open_pair(
    pre_source,
    post_source,
    *,
    selected_bands: tuple[SelectedBand, ...] | None,
    pre_sensor_type: str | None,
    post_sensor_type: str | None,
    numeric_range: tuple[float, float] | None,
    minimum_valid_fraction: float,
) -> NormalizedSatellitePair:
    _validate_source_georeferencing(pre_source, "PRE")
    _validate_source_georeferencing(post_source, "POST")
    bands = _resolve_bands(pre_source, post_source, selected_bands)
    _validate_sensor_pair(
        pre_source,
        post_source,
        bands,
        pre_sensor_type=pre_sensor_type,
        post_sensor_type=post_sensor_type,
    )
    value_range = _validate_numeric_range(numeric_range)

    if _grids_are_compatible(pre_source, post_source):
        pre, pre_valid = _read_selected(pre_source, tuple(band.pre_index for band in bands))
        post, post_valid = _read_selected(post_source, tuple(band.post_index for band in bands))
        valid = pre_valid & post_valid
        pre = _to_classifier_luminance(
            pre, pre_source, tuple(band.pre_index for band in bands), bands, valid, value_range
        )
        post = _to_classifier_luminance(
            post, post_source, tuple(band.post_index for band in bands), bands, valid, value_range
        )
        transform = _contract_transform(pre_source.transform)
        bounds = transform.grid_bounds(pre_source.width, pre_source.height)
        return create_normalized_pair(
            pre=pre,
            post=post,
            valid_mask=valid,
            working_crs=pre_source.crs.to_string(),
            transform=transform,
            bounds=bounds,
            width=pre_source.width,
            height=pre_source.height,
            selected_bands=bands,
            output_bands=("luminance",),
            representation="grayscale_uint8_0_255",
            pre_provenance=_provenance(
                pre_source, "pre", "compatible_fast_path", bands, "optical"
            ),
            post_provenance=_provenance(
                post_source, "post", "compatible_fast_path", bands, "optical"
            ),
            minimum_valid_fraction=minimum_valid_fraction,
        )

    working_crs = _select_working_crs(pre_source.crs, post_source.crs)
    pre_bounds = _bounds_in_crs(pre_source, working_crs, "PRE")
    post_bounds = _bounds_in_crs(post_source, working_crs, "POST")
    overlap = geographic_intersection(pre_bounds, post_bounds)

    pre_resolution = _resolution_in_crs(pre_source, working_crs)
    post_resolution = _resolution_in_crs(post_source, working_crs)
    resolution_x = max(pre_resolution[0], post_resolution[0])
    resolution_y = max(pre_resolution[1], post_resolution[1])
    if not all(math.isfinite(value) and value > 0 for value in (resolution_x, resolution_y)):
        raise ReprojectionError(
            "Could not derive a finite positive common resolution", input_role="PAIR"
        )

    width = int(math.floor((overlap[2] - overlap[0]) / resolution_x + 1e-9))
    height = int(math.floor((overlap[3] - overlap[1]) / resolution_y + 1e-9))
    if width < 1 or height < 1:
        raise InsufficientOverlapError(
            "Geographic overlap is smaller than one pixel at the safe common resolution",
            input_role="PAIR",
        )
    destination_transform = from_origin(
        overlap[0], overlap[3], resolution_x, resolution_y
    )
    transform = _contract_transform(destination_transform)
    bounds = transform.grid_bounds(width, height)

    pre, pre_valid = _reproject_selected(
        pre_source,
        tuple(band.pre_index for band in bands),
        working_crs,
        destination_transform,
        width,
        height,
    )
    post, post_valid = _reproject_selected(
        post_source,
        tuple(band.post_index for band in bands),
        working_crs,
        destination_transform,
        width,
        height,
    )
    valid = pre_valid & post_valid
    pre = _to_classifier_luminance(
        pre, pre_source, tuple(band.pre_index for band in bands), bands, valid, value_range
    )
    post = _to_classifier_luminance(
        post, post_source, tuple(band.post_index for band in bands), bands, valid, value_range
    )
    return create_normalized_pair(
        pre=pre,
        post=post,
        valid_mask=valid,
        working_crs=working_crs.to_string(),
        transform=transform,
        bounds=bounds,
        width=width,
        height=height,
        selected_bands=bands,
        output_bands=("luminance",),
        representation="grayscale_uint8_0_255",
        pre_provenance=_provenance(
            pre_source, "pre", "reprojected_to_common_grid", bands, "optical"
        ),
        post_provenance=_provenance(
            post_source, "post", "reprojected_to_common_grid", bands, "optical"
        ),
        minimum_valid_fraction=minimum_valid_fraction,
    )


def _validate_source_georeferencing(source, label: str) -> None:
    if source.crs is None:
        raise MissingGeoreferencingError(
            f"Raster {source.name} has no CRS", input_role=label
        )
    if source.transform is None or source.transform == Affine.identity():
        raise MissingGeoreferencingError(
            f"Raster {source.name} has no safe georeferencing transform", input_role=label
        )
    coefficients = tuple(source.transform)[:6]
    if not all(math.isfinite(value) for value in coefficients):
        raise UnsupportedTransformError(
            "Raster transform contains non-finite values", input_role=label
        )
    if math.isclose(
        source.transform.a * source.transform.e - source.transform.b * source.transform.d,
        0.0,
        abs_tol=1e-15,
    ):
        raise UnsupportedTransformError("Raster transform is singular", input_role=label)
    if source.width < 1 or source.height < 1 or source.count < 1:
        raise RasterReadError("Raster has no readable pixels or bands", input_role=label)
    _validated_bounds(tuple(source.bounds), label)


def _resolve_bands(pre_source, post_source, selected_bands):
    if selected_bands is None:
        pre_semantics = _semantic_band_indexes(pre_source)
        post_semantics = _semantic_band_indexes(post_source)
        if all(name in pre_semantics and name in post_semantics for name in ("red", "green", "blue")):
            return tuple(
                SelectedBand(
                    name,
                    pre_semantics[name],
                    post_semantics[name],
                    "optical_color",
                )
                for name in ("red", "green", "blue")
            )
        if "gray" in pre_semantics and "gray" in post_semantics:
            return (
                SelectedBand(
                    "luminance",
                    pre_semantics["gray"],
                    post_semantics["gray"],
                    "optical_intensity",
                ),
            )
        raise IncompatibleBandsError(
            "Current change detector requires matching RGB or grayscale bands; "
            "provide an explicit semantic band mapping when source metadata is insufficient",
            input_role="PAIR",
        )
    bands = tuple(selected_bands)
    if not bands:
        raise IncompatibleBandsError("At least one selected band is required")
    for band in bands:
        if band.pre_index > pre_source.count:
            raise IncompatibleBandsError(
                f"Selected {band.semantic} band {band.pre_index} exceeds the "
                f"{pre_source.count} available band(s)",
                input_role="PRE",
            )
        if band.post_index > post_source.count:
            raise IncompatibleBandsError(
                f"Selected {band.semantic} band {band.post_index} exceeds the "
                f"{post_source.count} available band(s)",
                input_role="POST",
            )
    semantics = {band.semantic.strip().lower() for band in bands}
    supported_gray = semantics <= {"gray", "grayscale", "greyscale", "luminance"}
    if semantics != {"red", "green", "blue"} and not (len(bands) == 1 and supported_gray):
        raise IncompatibleBandsError(
            "Current change detector supports only an explicit RGB triplet or one optical "
            "grayscale/luminance band"
        )
    return tuple(sorted(bands, key=lambda band: _band_order(band.semantic)))


def _semantic_band_indexes(source) -> dict[str, int]:
    semantics: dict[str, int] = {}
    for index, interpretation in enumerate(source.colorinterp, start=1):
        name = interpretation.name.lower()
        if name in {"red", "green", "blue", "gray"} and name not in semantics:
            semantics[name] = index
    return semantics


def _band_order(semantic: str) -> int:
    return {"red": 0, "green": 1, "blue": 2}.get(semantic.strip().lower(), 0)


def _validate_sensor_pair(
    pre_source,
    post_source,
    bands: tuple[SelectedBand, ...],
    *,
    pre_sensor_type: str | None,
    post_sensor_type: str | None,
) -> None:
    semantics = {band.semantic.strip().lower() for band in bands}
    rgb_is_explicit = semantics == {"red", "green", "blue"}
    pre_sensor = _sensor_type(pre_source, pre_sensor_type, rgb_is_explicit, "PRE")
    post_sensor = _sensor_type(post_source, post_sensor_type, rgb_is_explicit, "POST")
    if pre_sensor != post_sensor:
        raise IncompatibleSensorError(
            f"PRE sensor {pre_sensor!r} and POST sensor {post_sensor!r} are not interchangeable",
            input_role="PAIR",
        )
    if pre_sensor != "optical":
        raise IncompatibleSensorError(
            f"Current broad-area classifier supports optical intensity, not {pre_sensor!r}",
            input_role="PAIR",
        )


def _sensor_type(source, explicit: str | None, rgb_is_explicit: bool, label: str) -> str:
    declared = explicit or source.tags().get("SENSOR_TYPE") or source.tags().get("sensor_type")
    if declared is None and rgb_is_explicit:
        # Explicit red/green/blue color interpretation is itself an optical
        # representation declaration, not a guess from band position.
        return "optical"
    if declared is None:
        raise IncompatibleSensorError(
            "Grayscale raster requires an explicit sensor type; SAR and optical "
            "intensity cannot be inferred from pixel values",
            input_role=label,
        )
    normalized = str(declared).strip().lower()
    aliases = {"optical_rgb": "optical", "rgb": "optical", "multispectral_optical": "optical"}
    return aliases.get(normalized, normalized)


def _validate_numeric_range(numeric_range):
    if numeric_range is None:
        return None
    if (
        not isinstance(numeric_range, (tuple, list))
        or len(numeric_range) != 2
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in numeric_range)
        or numeric_range[0] >= numeric_range[1]
    ):
        raise IncompatibleRasterRepresentationError(
            "numeric_range must contain a finite common minimum and maximum"
        )
    return (float(numeric_range[0]), float(numeric_range[1]))


def _grids_are_compatible(pre_source, post_source) -> bool:
    return (
        pre_source.crs == post_source.crs
        and pre_source.width == post_source.width
        and pre_source.height == post_source.height
        and pre_source.transform.almost_equals(post_source.transform)
    )


def _read_selected(source, indexes: tuple[int, ...]):
    data = source.read(indexes=indexes)
    masks = source.read_masks(indexes=indexes)
    valid = np.all(masks > 0, axis=0)
    output = data[0] if len(indexes) == 1 else np.moveaxis(data, 0, -1)
    finite = np.isfinite(output) if output.ndim == 2 else np.all(np.isfinite(output), axis=2)
    return output, valid & finite


def _to_classifier_luminance(
    data,
    source,
    indexes: tuple[int, ...],
    bands: tuple[SelectedBand, ...],
    valid: NDArray[np.bool_],
    numeric_range: tuple[float, float] | None,
) -> NDArray[np.uint8]:
    channels = data[:, :, np.newaxis] if data.ndim == 2 else data
    normalized = np.empty(channels.shape, dtype=np.uint8)
    for output_index, source_index in enumerate(indexes):
        normalized[:, :, output_index] = _numeric_channel_to_uint8(
            channels[:, :, output_index],
            np.dtype(source.dtypes[source_index - 1]),
            valid,
            numeric_range,
        )

    semantics = [band.semantic.strip().lower() for band in bands]
    if len(semantics) == 1:
        luminance = normalized[:, :, 0]
    else:
        by_name = {name: normalized[:, :, index] for index, name in enumerate(semantics)}
        luminance = np.rint(
            0.299 * by_name["red"].astype(np.float32)
            + 0.587 * by_name["green"].astype(np.float32)
            + 0.114 * by_name["blue"].astype(np.float32)
        ).clip(0, 255).astype(np.uint8)
    # Invalid pixels carry a harmless storage value but remain observations only
    # through valid_mask.  The classifier must never evaluate these zeros.
    return np.where(valid, luminance, 0).astype(np.uint8, copy=False)


def _numeric_channel_to_uint8(
    channel,
    source_dtype: np.dtype,
    valid: NDArray[np.bool_],
    numeric_range: tuple[float, float] | None,
) -> NDArray[np.uint8]:
    if source_dtype.kind == "b":
        raise IncompatibleRasterRepresentationError("Boolean satellite bands are unsupported")
    if numeric_range is not None:
        lower, upper = numeric_range
    elif source_dtype.kind in {"u", "i"}:
        limits = np.iinfo(source_dtype)
        lower, upper = float(limits.min), float(limits.max)
    elif source_dtype.kind == "f":
        raise IncompatibleRasterRepresentationError(
            "Floating-point satellite bands require an explicit common numeric_range"
        )
    else:
        raise IncompatibleRasterRepresentationError(
            f"Unsupported satellite numeric dtype: {source_dtype}"
        )
    values = np.asarray(channel, dtype=np.float64)
    if not np.isfinite(values[valid]).all():
        raise IncompatibleRasterRepresentationError(
            "Valid satellite pixels must contain finite numeric values"
        )
    scaled = (values - lower) * (255.0 / (upper - lower))
    scaled = np.where(valid, scaled, 0.0)
    return np.rint(np.clip(scaled, 0, 255)).astype(np.uint8)


def _select_working_crs(pre_crs: RasterioCRS, post_crs: RasterioCRS) -> RasterioCRS:
    # Prefer an existing projected grid to avoid measuring resolution in angular
    # units when one source already supplies an appropriate linear CRS.
    if pre_crs.is_projected:
        return pre_crs
    if post_crs.is_projected:
        return post_crs
    return pre_crs


def _bounds_in_crs(source, destination_crs: RasterioCRS, label: str) -> Bounds:
    try:
        if source.crs == destination_crs:
            return _validated_bounds(tuple(source.bounds), label)
        transformed = transform_bounds(
            source.crs, destination_crs, *source.bounds, densify_pts=21
        )
        return _validated_bounds(tuple(transformed), label)
    except (RasterioError, ValueError) as exc:
        raise ReprojectionError(
            f"Could not transform bounds from {source.crs} to {destination_crs}: {exc}",
            input_role=label,
        ) from exc


def _resolution_in_crs(source, destination_crs: RasterioCRS) -> tuple[float, float]:
    if source.crs == destination_crs:
        return (
            math.hypot(source.transform.a, source.transform.d),
            math.hypot(source.transform.b, source.transform.e),
        )
    try:
        transform, _, _ = calculate_default_transform(
            source.crs,
            destination_crs,
            source.width,
            source.height,
            *source.bounds,
        )
    except (RasterioError, ValueError) as exc:
        raise ReprojectionError(
            f"Could not calculate source resolution in {destination_crs}: {exc}",
            input_role="PAIR",
        ) from exc
    return (math.hypot(transform.a, transform.d), math.hypot(transform.b, transform.e))


def _reproject_selected(
    source,
    indexes: tuple[int, ...],
    destination_crs: RasterioCRS,
    destination_transform: Affine,
    width: int,
    height: int,
):
    destination = np.full((len(indexes), height, width), np.nan, dtype=np.float32)
    for output_index, source_index in enumerate(indexes):
        source_values = source.read(source_index).astype(np.float32)
        source_band_valid = source.read_masks(source_index) > 0
        source_values[~source_band_valid] = np.nan
        reproject(
            source=source_values,
            destination=destination[output_index],
            src_transform=source.transform,
            src_crs=source.crs,
            src_nodata=np.nan,
            dst_transform=destination_transform,
            dst_crs=destination_crs,
            dst_nodata=np.nan,
            resampling=Resampling.bilinear,
            init_dest_nodata=True,
        )

    source_valid = np.all(source.read_masks(indexes=indexes) > 0, axis=0).astype(np.float32)
    destination_coverage = np.zeros((height, width), dtype=np.float32)
    reproject(
        source=source_valid,
        destination=destination_coverage,
        src_transform=source.transform,
        src_crs=source.crs,
        dst_transform=destination_transform,
        dst_crs=destination_crs,
        dst_nodata=0,
        resampling=Resampling.bilinear,
        init_dest_nodata=True,
    )
    output = destination[0] if len(indexes) == 1 else np.moveaxis(destination, 0, -1)
    finite = np.isfinite(output) if output.ndim == 2 else np.all(np.isfinite(output), axis=2)
    return output, (destination_coverage >= 1.0 - 1e-6) & finite


def _contract_transform(transform: Affine) -> AffineTransform:
    return AffineTransform(
        float(transform.a),
        float(transform.b),
        float(transform.c),
        float(transform.d),
        float(transform.e),
        float(transform.f),
    )


def _provenance(
    source,
    phase: str,
    normalization_path: str,
    bands: tuple[SelectedBand, ...],
    sensor: str,
) -> RasterProvenance:
    return RasterProvenance(
        source_id=f"{phase}:{Path(source.name).name}",
        source_path=str(source.name),
        driver=source.driver,
        original_crs=source.crs.to_string(),
        original_bounds=tuple(map(float, source.bounds)),
        original_shape=(source.height, source.width, source.count),
        original_dtype=",".join(source.dtypes),
        sensor=sensor,
        metadata={
            "transform": tuple(map(float, tuple(source.transform)[:6])),
            "nodata": source.nodata,
            "normalization_path": normalization_path,
            "selected_band_semantics": [band.semantic for band in bands],
        },
    )


def _validated_bounds(bounds: Bounds, label: str) -> Bounds:
    if not isinstance(bounds, (tuple, list)) or len(bounds) != 4:
        raise MissingGeoreferencingError(
            "Bounds must contain west,south,east,north", input_role=label
        )
    if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bounds):
        raise MissingGeoreferencingError(
            "Bounds must contain four finite numbers", input_role=label
        )
    west, south, east, north = map(float, bounds)
    if west >= east or south >= north:
        raise MissingGeoreferencingError(
            f"Bounds are empty or inverted: {bounds}", input_role=label
        )
    return (west, south, east, north)


__all__ = [
    "AffineTransform",
    "Bounds",
    "IncompatibleBandsError",
    "IncompatibleRasterRepresentationError",
    "IncompatibleSensorError",
    "InsufficientOverlapError",
    "InsufficientValidCoverageError",
    "InvalidOutputGridError",
    "MissingGeoreferencingError",
    "NoGeographicOverlapError",
    "NormalizedSatellitePair",
    "RasterProvenance",
    "RasterReadError",
    "ReprojectionError",
    "SatelliteNormalizationError",
    "SelectedBand",
    "UnsupportedCRSError",
    "UnsupportedTransformError",
    "create_normalized_pair",
    "geographic_intersection",
    "normalize_satellite_pair",
    "normalize_transferred_pair",
]
