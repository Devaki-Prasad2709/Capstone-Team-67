"""
broad_area_classifier.py

Satellite broad-area destruction classification. NO MODEL, per the
architecture doc's "genuinely trainingless" recommendation: this is pure
pre/post image differencing, tiled into a grid, bucketed into severity
labels. Deliberately dashboard-only -- never touches the TGNN, per the
locked architecture rule (satellite gives regional context at a spatial
resolution the graph doesn't operate at).

Method:
  1. Load a pre-disaster and post-disaster image of the SAME area
     (caller's responsibility that they're already roughly aligned --
     see ALIGNMENT ASSUMPTION below).
  2. Convert both to grayscale, tile into an NxN grid.
  3. For each tile, compute a simple structural difference score (mean
     absolute pixel difference, normalized to [0,1]).
  4. Bucket into low/moderate/severe using disclosed, tunable thresholds.
  5. Emit a GeoJSON FeatureCollection of grid-cell polygons, matching the
     dashboard's satellite overlay contract (coarse, low-opacity regional
     layer -- see dashboard requirements spec).

ALIGNMENT ASSUMPTION (disclosed, not hidden): this assumes the pre/post
images already cover the same geographic extent at the same resolution
(e.g. both cropped from the same source, or both pre-orthorectified to the
same bounds). It does NOT do image registration/alignment -- if your real
pre/post pairs are taken from different angles/zooms, add a registration
step before this, or the tile-level scores will be meaningless. This is
exactly the kind of "no fake precision" disclosure the rest of this
project has been careful about -- flagged rather than silently assumed
correct.
"""

from dataclasses import dataclass
from PIL import Image
import numpy as np


# Disclosed, tunable thresholds -- not measured, not calibrated against
# real disaster imagery yet. Revisit once you have any real pre/post pair
# to sanity-check against.
SEVERITY_THRESHOLDS = {
    "low": 0.15,       # diff score below this -> "low"
    "moderate": 0.40,  # below this -> "moderate", at/above -> "severe"
}

DEFAULT_GRID_SIZE = 8  # 8x8 tiles -- coarse by design, matches the
                        # "regional, not building-level" role satellite
                        # plays in this architecture


@dataclass
class TileResult:
    row: int
    col: int
    diff_score: float
    severity: str
    # Geographic bounds of this tile, filled in by classify_area() using
    # the caller-supplied AOI bounds -- kept separate from the raw pixel
    # math so this module has no CRS/projection dependency of its own.
    lon_min: float = None
    lon_max: float = None
    lat_min: float = None
    lat_max: float = None


def _severity_label(score: float) -> str:
    if score < SEVERITY_THRESHOLDS["low"]:
        return "low"
    elif score < SEVERITY_THRESHOLDS["moderate"]:
        return "moderate"
    return "severe"


def _tile_diff_scores(pre_img: np.ndarray, post_img: np.ndarray, grid_size: int) -> list:
    """
    pre_img, post_img: 2D grayscale numpy arrays, SAME shape (caller must
    ensure this -- see module docstring's alignment assumption).
    Returns a flat list of TileResult (row, col, diff_score, severity),
    row-major order.
    """
    h, w = pre_img.shape
    if isinstance(grid_size, bool) or not isinstance(grid_size, (int, np.integer)) or grid_size <= 0:
        raise ValueError("grid_size must be a positive integer.")
    if grid_size > min(h, w):
        raise ValueError(
            f"grid_size ({grid_size}) must not exceed the smaller image dimension ({min(h, w)})."
        )
    results = []

    diff = np.abs(pre_img.astype(np.float32) - post_img.astype(np.float32)) / 255.0

    for row in range(grid_size):
        for col in range(grid_size):
            # Proportional integer boundaries cover each pixel exactly once.
            # Pixel rounding does not change the evenly divided AOI grid.
            y0, y1 = row * h // grid_size, (row + 1) * h // grid_size
            x0, x1 = col * w // grid_size, (col + 1) * w // grid_size
            tile_diff = diff[y0:y1, x0:x1]
            score = float(tile_diff.mean())
            results.append(TileResult(row=row, col=col, diff_score=score, severity=_severity_label(score)))

    return results


def classify_area(
    pre_image_path: str,
    post_image_path: str,
    aoi_bounds: tuple,   # (lon_min, lat_min, lon_max, lat_max) -- WGS84
    grid_size: int = DEFAULT_GRID_SIZE,
) -> dict:
    """
    Main entry point. Returns a GeoJSON FeatureCollection ready for the
    dashboard's satellite overlay endpoint -- one Polygon feature per grid
    tile, with `diff_score` and `severity` properties.

    aoi_bounds: the geographic bounding box the two images cover, in WGS84
                (lon_min, lat_min, lon_max, lat_max). Used only to place
                the resulting grid on the map -- has no effect on the pixel
                math itself.
    """
    with Image.open(pre_image_path) as source:
        pre = source.convert("L")   # grayscale; no resizing or registration
    with Image.open(post_image_path) as source:
        post = source.convert("L")

    if pre.size != post.size:
        raise ValueError(
            f"Pre/post image sizes differ ({pre.size} vs {post.size}) -- "
            f"per this module's alignment assumption, they must already "
            f"cover the same extent at the same resolution. Resize/align "
            f"before calling classify_area(), don't silently stretch here."
        )

    pre_arr = np.array(pre)
    post_arr = np.array(post)

    tiles = _tile_diff_scores(pre_arr, post_arr, grid_size)

    lon_min, lat_min, lon_max, lat_max = aoi_bounds
    lon_step = (lon_max - lon_min) / grid_size
    lat_step = (lat_max - lat_min) / grid_size

    features = []
    for t in tiles:
        # row 0 = top of image = highest latitude
        tile_lat_max = lat_max - t.row * lat_step
        tile_lat_min = tile_lat_max - lat_step
        tile_lon_min = lon_min + t.col * lon_step
        tile_lon_max = tile_lon_min + lon_step

        features.append({
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [tile_lon_min, tile_lat_min],
                    [tile_lon_max, tile_lat_min],
                    [tile_lon_max, tile_lat_max],
                    [tile_lon_min, tile_lat_max],
                    [tile_lon_min, tile_lat_min],
                ]],
            },
            "properties": {
                "diff_score": round(t.diff_score, 4),
                "severity": t.severity,
                "row": t.row,
                "col": t.col,
            },
        })

    return {"type": "FeatureCollection", "features": features}


if __name__ == "__main__":
    # Quick self-test with two synthetic images (not real satellite data --
    # this only proves the tiling/scoring/GeoJSON-emission mechanics work).
    import numpy as np
    from PIL import Image
    import tempfile, os

    rng = np.random.default_rng(42)
    pre = (rng.uniform(100, 150, size=(400, 400))).astype(np.uint8)
    post = pre.copy()
    # Synthetic pixel changes: one moderate tile and fifteen low tiles.
    post[0:150, 0:150] = (rng.uniform(0, 255, size=(150, 150))).astype(np.uint8)

    with tempfile.TemporaryDirectory() as tmp:
        pre_path = os.path.join(tmp, "pre.png")
        post_path = os.path.join(tmp, "post.png")
        Image.fromarray(pre).save(pre_path)
        Image.fromarray(post).save(post_path)

        result = classify_area(pre_path, post_path, aoi_bounds=(77.69, 12.70, 77.71, 12.72), grid_size=4)
        print(f"Generated {len(result['features'])} tiles")
        for f in result["features"]:
            print(f"  row={f['properties']['row']} col={f['properties']['col']} "
                  f"score={f['properties']['diff_score']} severity={f['properties']['severity']}")
