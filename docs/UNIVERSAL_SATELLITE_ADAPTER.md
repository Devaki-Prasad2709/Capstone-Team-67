# Universal Satellite Adapter

## Purpose and non-goals

The Universal Satellite Adapter converts a compatible optical PRE/POST raster
pair into one spatially aligned, masked numeric representation. It keeps
dataset-specific geometry, band, datatype, CRS, and NoData logic out of the
broad-area change classifier.

The adapter does not calculate difference scores, assign severity, infer that
unrelated bands or sensors are interchangeable, classify infrastructure,
create graph observations, feed TGNN inference, or fabricate georeferencing or
pixels outside either image. Change scoring remains in
`core/satellite/broad_area_classifier.py`. SpaceNet is a supported dataset,
not a separate mandatory pipeline.

## Accepted PRE/POST input contract

The file entry point is `normalize_satellite_pair(pre_path, post_path, ...)`.
Each raster must provide:

- readable pixels and at least one band;
- an explicit, supported CRS;
- a finite, invertible, non-identity affine transform;
- finite, non-empty geographic bounds;
- overlapping geographic coverage;
- matching optical band semantics, declared by color interpretation or an
  explicit `SelectedBand` mapping; and
- sensor representations compatible with each other and with optical
  intensity comparison.

Band indexes are one-based. Supported mappings are an optical RGB triplet or
one optical grayscale/luminance band. A grayscale source requires an explicit
optical sensor declaration because values alone cannot distinguish optical
imagery from SAR. A custom mapping looks like:

```python
bands = (
    SelectedBand("red", 4, 3, "optical_color"),
    SelectedBand("green", 3, 2, "optical_color"),
    SelectedBand("blue", 2, 1, "optical_color"),
)
pair = normalize_satellite_pair(
    pre_path,
    post_path,
    selected_bands=bands,
    pre_sensor_type="optical",
    post_sensor_type="optical",
)
```

The production producer transports JPEG representations through MinIO/Kafka.
Because JPEG does not retain GeoTIFF tags, `normalize_transferred_pair`
requires explicit WGS84 PRE and POST bounds from the event contract. It
restores only those declared footprints and does not guess other metadata.

## Normalized output fields

`NormalizedSatellitePair` contains:

| Field | Meaning |
| --- | --- |
| `pre`, `post` | Aligned arrays with identical shape; currently one `uint8` luminance channel |
| `valid_mask` | Boolean `(height, width)` mask where both sources are valid and finite |
| `working_crs` | CRS shared by both normalized arrays |
| `transform` | Six-parameter affine grid-to-CRS transform |
| `bounds` | `(west, south, east, north)` in `working_crs` |
| `width`, `height` | Common grid dimensions |
| `selected_bands` | Explicit PRE/POST source band indexes and semantics |
| `output_bands` | Normalized channel names; currently `("luminance",)` |
| `representation` | Current value contract: `grayscale_uint8_0_255` |
| `pre_provenance`, `post_provenance` | Source path/ID, driver, original CRS, bounds, shape, dtype, sensor, and normalization path |

Construction validates the arrays, mask, dimensions, transform-derived
bounds, CRS, band declarations, representation, and provenance together. It
has no array-resize fallback.

## CRS, overlap, grid, and resolution policy

If PRE and POST already share CRS, dimensions, and an equivalent affine
transform, the compatible fast path reads them directly without reprojection.
Band and sensor validation and the combined valid mask still apply.

Otherwise:

1. An existing projected source CRS is preferred. If neither source is
   projected, PRE's CRS becomes the working CRS.
2. Bounds are transformed into that CRS with densified edges.
3. Only the geographic intersection is retained; no overlap fails.
4. The coarser PRE/POST pixel size is selected independently for X and Y,
   avoiding invented detail from upsampling.
5. Dimensions are floored to whole pixels, clipping inward so the grid cannot
   extend beyond shared coverage.
6. Both sources are reprojected onto that one grid.

Different array dimensions never justify stretching one image to the other.
Overlap smaller than one output pixel at the safe common resolution fails.

## Resampling policy

Raster values use Rasterio/GDAL bilinear reprojection. Invalid source values
are changed to `NaN` first so NoData cannot act like a zero observation.
Coverage is reprojected separately. A destination pixel is valid only when
coverage is effectively complete (`>= 1 - 1e-6`) and all selected values are
finite. This conservative rule excludes partially supported edge pixels.

## Band, sensor, and numeric compatibility policy

Only explicit optical RGB or optical grayscale/luminance is supported. Extra
source bands are ignored. SAR, RGB, multispectral, and hyperspectral data are
not silently interpreted as equivalent. Supporting another representation
requires a semantic adapter and suitable classifier, not a sensor-name alias.

Selected channels become a common `uint8` representation. Integer data uses
its dtype range. Floating-point data requires one explicit common
`numeric_range`; independent per-image min/max normalization is forbidden
because it changes comparison meaning. Boolean rasters fail. RGB becomes
luminance with fixed `0.299 R + 0.587 G + 0.114 B` weights. Classifier scoring
and severity thresholds are not changed by normalization.

## NoData and valid-mask behavior

Rasterio source masks preserve declared NoData and dataset-mask information.
All selected bands must be valid within each image, and both images must be
valid for the combined mask. Invalid array locations contain zero only as a
storage value; the mask prevents the classifier from observing that value.

`minimum_valid_fraction` can reject the whole pair when corresponding valid
coverage is insufficient. The classifier separately applies a per-cell
threshold, currently `0.95`.

## Failure and unknown behavior

Domain-specific errors identify PRE, POST, PAIR, or OUTPUT and the violated
requirement. They cover unreadable/corrupt inputs, missing georeferencing,
unsupported CRS or transform, reprojection failure, invalid output grids, no
or insufficient overlap, insufficient valid coverage, incompatible bands,
and incompatible sensor or numeric representations.

A grid cell below classifier coverage is emitted as:

- `diff_score: null`;
- `severity: "unknown"`;
- `quality: "insufficient_coverage"`; and
- its measured valid fraction and valid-pixel count.

Insufficient data never becomes a low-change score.

## SpaceNet as one supported case

SpaceNet 8 regression coverage includes contract tile `0_10_2` and active
Louisiana scenario tile `2_23_44`. Their PRE/POST dimensions and transforms
differ, so they exercise geographic alignment through the same adapter as any
other compatible pair. Active-scenario events preserve the real dataset,
original TIFF path/size/SHA-256, derived MinIO JPEG checksum, footprint, and
the fact that scenario time is simulated.

`core/satellite/spacenet_adapter.py` is only a compatibility wrapper for old
callers. It delegates to the universal normalizer and generic classifier.

## Normalization versus change detection

The production path is:

```text
PRE/POST source references
  -> Universal Satellite Adapter
  -> NormalizedSatellitePair
  -> generic broad-area classifier
  -> EPSG:4326 GeoJSON on satellite-change-results
  -> dashboard satellite API and map layer
```

The adapter owns compatibility, alignment, numeric representation, and valid
coverage. The classifier owns absolute-difference scoring, severity labels,
per-cell unknown decisions, and WGS84 GeoJSON creation.

## Dashboard-only architectural boundary

`satellite-change-results` is dashboard-only evidence. Spark, observation
ingestion, graph snapshots, TGNN, and NLP/social alerts do not subscribe to it.
It cannot change node damage, load, capacity, utilization, stress, status,
risk, graph observations, or confirmed hotspots. Its contract declares
`tgnn_integration: "none"` and contains no graph-node association. Offline
evaluation code may read the result for reporting but cannot route it back
into operational state.

## Adding another compatible optical raster source

1. Inspect documented CRS, transform, bounds, NoData, dtype, color
   interpretation, and sensor type.
2. Confirm PRE/POST geographic overlap and physical band equivalence. Do not
   start from array dimensions.
3. Supply `SelectedBand` mappings if color interpretation is insufficient.
4. Declare optical sensor types when trustworthy metadata does not. Never
   label SAR as optical merely to pass validation.
5. Supply one documented common numeric range for floating-point data.
6. Normalize with `normalize_satellite_pair`, then call
   `classify_normalized_pair`; do not add scoring inside the adapter.
7. Add synthetic geometry, band, NoData, and failure tests plus a guarded
   real-data regression.
8. Preserve dataset, source path, checksum, real/simulated fields, timestamp,
   and footprint in producer provenance.
9. Keep the result on the dashboard topic unless a separately designed and
   tested graph integration is explicitly approved.

## Dependency audit

The adapter adds `rasterio==1.4.3`. Rasterio provides the GDAL-backed reader,
CRS-aware bounds transformation, affine-grid handling, NoData masks, and
reprojection/resampling required by the geographic contract. Existing NumPy,
PyProj, Pillow, OpenCV, and Shapely dependencies cannot safely replace that
combination without implementing another raster engine. No other direct
runtime dependency was added for the adapter.
