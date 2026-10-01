"""SpaceNet compatibility entry points over the universal satellite pipeline.

The restricted ``read_geotiff_grid`` helper remains for legacy SpaceNet
metadata checks.  Classification itself has no mandatory SpaceNet path:
public wrappers delegate to the universal normalizer and generic classifier.
No graph or observation components are imported here.
"""

from dataclasses import dataclass
import math

from PIL import Image

from core.satellite.broad_area_classifier import classify_normalized_pair
from core.satellite.normalizer import normalize_satellite_pair, normalize_transferred_pair


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


def classify_spacenet_pair(pre_image_path, post_image_path, grid_size=4,
                           min_valid_fraction=0.95):
    """Compatibility wrapper for existing SpaceNet callers.

    SpaceNet now passes through the same universal normalizer and generic
    classifier as every supported raster pair.
    """
    pair = normalize_satellite_pair(
        pre_image_path,
        post_image_path,
        minimum_valid_fraction=0.0,
    )
    return classify_normalized_pair(pair, grid_size, min_valid_fraction)


def classify_transferred_pair(
    pre_image_bytes: bytes,
    post_image_bytes: bytes,
    pre_bbox,
    post_bbox,
    grid_size=8,
    min_valid_fraction=0.95,
):
    """Compatibility wrapper for the existing Kafka/MinIO image contract."""
    pair = normalize_transferred_pair(
        pre_image_bytes,
        post_image_bytes,
        pre_bbox,
        post_bbox,
        minimum_valid_fraction=0.0,
    )
    return classify_normalized_pair(pair, grid_size, min_valid_fraction)


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
