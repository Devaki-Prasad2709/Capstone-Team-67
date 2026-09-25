"""Dashboard-only alignment for north-up, WGS84, PixelIsArea SpaceNet RGB TIFFs.

This is a deliberately restricted GeoTIFF reader, not a raster reprojection
engine. Unsupported georeferencing fails closed: use Rasterio for other CRSs,
rotations, PixelIsPoint or sensor/RPC models. No graph or observation imports.
Metadata alignment does not correct residual sensor registration or illumination.
"""

from dataclasses import dataclass
from io import BytesIO
import math

import numpy as np
from PIL import Image

from core.satellite.broad_area_classifier import _tile_diff_scores


@dataclass(frozen=True)
class GeoGrid:
    width: int
    height: int
    west: float
    north: float
    dx: float
    dy: float
    nodata: float | None = None

    @property
    def bounds(self):
        return (self.west, self.north - self.height * self.dy,
                self.west + self.width * self.dx, self.north)


def _read_grid(image):
    tags = image.tag_v2
    if image.mode != "RGB" or tuple(tags.get(258, ())) != (8, 8, 8):
        raise ValueError("SpaceNet adapter requires 8-bit RGB TIFFs")
    if tags.get(274, 1) != 1 or getattr(image, "n_frames", 1) != 1:
        raise ValueError("Only top-left oriented, single-image TIFFs are supported")
    if 34264 in tags or 50844 in tags:
        raise ValueError("Transformation matrices/RPC models require a raster geospatial reader")
    directory = tags.get(34735, ())
    if (len(directory) < 4 or tuple(directory[:2]) != (1, 1)
            or directory[2] not in (0, 1) or len(directory) != 4 + 4 * directory[3]):
        raise ValueError("Missing or malformed GeoKeyDirectoryTag")
    keys = {}
    for i in range(4, len(directory), 4):
        key, location, count, value = directory[i:i + 4]
        if key in keys:
            raise ValueError("Duplicate GeoTIFF keys")
        keys[key] = value if location == 0 and count == 1 else None
    if any(keys.get(k) != v for k, v in {1024: 2, 1025: 1, 2048: 4326, 2054: 9102}.items()):
        raise ValueError("Requires geographic WGS84/EPSG:4326, degrees and PixelIsArea")
    scale, tie = tags.get(33550, ()), tags.get(33922, ())
    if len(scale) != 3 or len(tie) != 6 or not all(math.isfinite(v) for v in (*scale, *tie)):
        raise ValueError("Requires finite ModelPixelScaleTag and exactly one ModelTiepointTag")
    dx, dy, dz = scale
    i, j, k, x, y, z = tie
    if dx <= 0 or dy <= 0 or dz != 0 or k != 0 or z != 0:
        raise ValueError("Only north-up 2D pixel-scale/tiepoint transforms are supported")
    nodata = None
    if 42113 in tags:
        nodata = float(str(tags[42113]).rstrip("\x00"))
        if not math.isfinite(nodata) or not 0 <= nodata <= 255:
            raise ValueError("Unsupported RGB NoData value")
    grid = GeoGrid(image.width, image.height, x - i * dx, y + j * dy, dx, dy, nodata)
    west, south, east, north = grid.bounds
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Invalid WGS84 raster extent")
    return grid


def read_geotiff_grid(path):
    """Read and validate the supported metadata without decoding image pixels."""
    with Image.open(path) as image:
        return _read_grid(image)


def _load(path):
    with Image.open(path) as image:
        grid = _read_grid(image)
        rgb = np.asarray(image)
        gray = np.asarray(image.convert("L"), dtype=np.float64)
    # Conservative quality policy: all-black RGB is uninformative, not assumed
    # to be metadata-declared NoData. Exclude it explicitly in both images.
    valid = ~np.all(rgb == 0, axis=2)
    if grid.nodata is not None:
        valid &= ~np.any(rgb == grid.nodata, axis=2)
    return grid, gray, valid


def _sample_post(pre_grid, post_grid, post, valid):
    """Inverse-map PRE centres to POST centre indices; bilinear interpolation.

    No extrapolation, edge stretching or filling: samples beyond the source
    centre domain are invalid. All positively weighted contributors must be valid.
    """
    cols = ((pre_grid.west - post_grid.west)
            + (np.arange(pre_grid.width) + 0.5) * pre_grid.dx) / post_grid.dx - 0.5
    rows = ((post_grid.north - pre_grid.north)
            + (np.arange(pre_grid.height) + 0.5) * pre_grid.dy) / post_grid.dy - 0.5
    inside = ((rows[:, None] >= 0) & (rows[:, None] <= post_grid.height - 1)
              & (cols[None, :] >= 0) & (cols[None, :] <= post_grid.width - 1))
    cols = np.clip(cols, 0, post_grid.width - 1)
    rows = np.clip(rows, 0, post_grid.height - 1)
    x0, y0 = np.floor(cols).astype(int), np.floor(rows).astype(int)
    x1, y1 = np.minimum(x0 + 1, post_grid.width - 1), np.minimum(y0 + 1, post_grid.height - 1)
    fx, fy = cols - x0, rows - y0
    sampled = np.zeros((pre_grid.height, pre_grid.width), dtype=np.float64)
    for ys, xs, weight in [
        (y0, x0, (1 - fy[:, None]) * (1 - fx[None, :])),
        (y0, x1, (1 - fy[:, None]) * fx[None, :]),
        (y1, x0, fy[:, None] * (1 - fx[None, :])),
        (y1, x1, fy[:, None] * fx[None, :]),
    ]:
        sampled += post[ys[:, None], xs[None, :]] * weight
        inside &= valid[ys[:, None], xs[None, :]] | (weight == 0)
    return sampled, inside


def _classify_loaded_pair(
    pre_grid, pre, pre_valid, post_grid, post, post_valid, grid_size, min_valid_fraction
):
    if (isinstance(grid_size, bool) or not isinstance(grid_size, (int, np.integer))
            or not 1 <= grid_size <= min(pre.shape)):
        raise ValueError("grid_size must be a positive integer within PRE dimensions")
    if not math.isfinite(min_valid_fraction) or not 0 < min_valid_fraction <= 1:
        raise ValueError("min_valid_fraction must be in (0, 1]")
    a, b = pre_grid.bounds, post_grid.bounds
    overlap = (max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3]))
    if overlap[0] >= overlap[2] or overlap[1] >= overlap[3]:
        raise ValueError("PRE and POST have no geographic overlap")
    aligned, valid = _sample_post(pre_grid, post_grid, post, post_valid)
    valid &= pre_valid
    if not valid.any():
        raise ValueError("No valid corresponding pixels to compare")
    features = []
    for row in range(grid_size):
        y0, y1 = row * pre_grid.height // grid_size, (row + 1) * pre_grid.height // grid_size
        for col in range(grid_size):
            x0, x1 = col * pre_grid.width // grid_size, (col + 1) * pre_grid.width // grid_size
            mask = valid[y0:y1, x0:x1]
            count = int(mask.sum())
            fraction = count / mask.size
            score, severity = None, "unknown"
            if count and fraction >= min_valid_fraction:
                tile = _tile_diff_scores(pre[y0:y1, x0:x1][mask][None, :],
                                         aligned[y0:y1, x0:x1][mask][None, :], 1)[0]
                score, severity = round(tile.diff_score, 4), tile.severity
            west, east = pre_grid.west + x0 * pre_grid.dx, pre_grid.west + x1 * pre_grid.dx
            north, south = pre_grid.north - y0 * pre_grid.dy, pre_grid.north - y1 * pre_grid.dy
            features.append({"type": "Feature", "geometry": {
                "type": "Polygon", "coordinates": [[[west, south], [east, south],
                    [east, north], [west, north], [west, south]]]}, "properties": {
                "row": row, "col": col, "diff_score": score, "severity": severity,
                "valid_fraction": fraction, "valid_pixels": count,
                "quality": "sufficient_coverage" if score is not None else "insufficient_coverage",
            }})
    return {"type": "FeatureCollection", "features": features, "metadata": {
        "source": "SpaceNet8", "crs": "EPSG:4326", "reference_grid": "PRE",
        "resampling": "POST bilinear at PRE pixel centres; no extrapolation",
        "pre_bounds": a, "post_bounds": b, "overlap_bounds": overlap,
        "pre_size": [pre_grid.width, pre_grid.height],
        "post_size": [post_grid.width, post_grid.height],
        "min_valid_fraction": min_valid_fraction,
        "valid_fraction": float(valid.mean()),
        "mask_policy": "exclude declared NoData in any band, all-black RGB, and invalid interpolation support",
        "interpretation": "uncalibrated radiometric change; not destruction probability",
    }}


def classify_spacenet_pair(pre_image_path, post_image_path, grid_size=4,
                           min_valid_fraction=0.95):
    """Return WGS84 dashboard GeoJSON; low-coverage cells have null scores.

    Scores reuse the generic classifier's grayscale MAD and severity thresholds.
    Polygons follow exact integer pixel boundaries, including non-divisible grids.
    This is radiometric change evidence, not calibrated destruction probability.
    """
    return _classify_loaded_pair(
        *_load(pre_image_path),
        *_load(post_image_path),
        grid_size,
        min_valid_fraction,
    )


def _grid_from_bbox(width: int, height: int, bbox) -> GeoGrid:
    if (
        not isinstance(bbox, (list, tuple))
        or len(bbox) != 4
        or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in bbox)
    ):
        raise ValueError("Transported satellite image requires a finite WGS84 bbox")
    west, south, east, north = map(float, bbox)
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("Transported satellite bbox must be west,south,east,north")
    return GeoGrid(width, height, west, north, (east - west) / width, (north - south) / height)


def _load_transferred_rgb(image_bytes: bytes, bbox):
    """Decode producer-transferred RGB/JPEG bytes with explicit footprint metadata."""
    with Image.open(BytesIO(image_bytes)) as image:
        rgb = np.asarray(image.convert("RGB"))
        gray = np.asarray(image.convert("L"), dtype=np.float64)
    grid = _grid_from_bbox(rgb.shape[1], rgb.shape[0], bbox)
    return grid, gray, ~np.all(rgb == 0, axis=2)


def classify_transferred_pair(
    pre_image_bytes: bytes,
    post_image_bytes: bytes,
    pre_bbox,
    post_bbox,
    grid_size=8,
    min_valid_fraction=0.95,
):
    """Analyze the actual Kafka/MinIO JPEG payloads as broad-area evidence.

    GeoTIFF tags do not survive the existing TIFF-to-JPEG producer contract, so
    the producer's explicit, validated WGS84 footprints define each transported
    raster grid. The output remains dashboard-only and is never a TGNN feature.
    """
    return _classify_loaded_pair(
        *_load_transferred_rgb(pre_image_bytes, pre_bbox),
        *_load_transferred_rgb(post_image_bytes, post_bbox),
        grid_size,
        min_valid_fraction,
    )


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pre")
    parser.add_argument("post")
    parser.add_argument("--output", required=True)
    parser.add_argument("--grid-size", type=int, default=4)
    args = parser.parse_args()
    result = classify_spacenet_pair(args.pre, args.post, args.grid_size)
    Path(args.output).write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps(result["metadata"], indent=2))
    print(f"Wrote {len(result['features'])} dashboard cells to {args.output}")
