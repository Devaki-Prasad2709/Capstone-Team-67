"""
gis_loader.py

Loads GIS infrastructure data from a GeoJSON source and reprojects every
geometry into a single working projected CRS, per the locked Final
Architecture rule:

    "All spatial calculations happen in one projected working CRS.
     WGS84 conversion happens at exactly one point on the way out."

This module is the *entry* point of that rule (WGS84 -> working CRS).
The overlay module (overlay/gis_overlay.py) is the *exit* point
(working CRS -> WGS84), and nowhere else in the codebase should convert
between coordinate systems.

Today this loads the DEMO_PLACEHOLDER_FIXTURE (gis/fixtures/demo_gis.geojson).
When the real GIS dataset is available, point `load_gis(path)` at it instead --
as long as it's valid GeoJSON with the same `feature_class` property
convention (infrastructure / road_segment / building), nothing else in the
pipeline needs to change. If the real data is a different format
(shapefile, GeoPackage, PostGIS), only this file needs a new loader function;
everything downstream consumes the same `GISData` object either way.
"""

import json
import math
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from pyproj import Transformer
from shapely.geometry import shape, Point, LineString, Polygon
from shapely.ops import transform as shapely_transform

VALID_NODE_TYPES = frozenset({
    "power", "road", "hospital", "telecom", "water", "social",
})


# ---------------------------------------------------------------------------
# Working CRS
# ---------------------------------------------------------------------------
# Legacy default for manually constructed GISData and two-argument helpers.
# Loaded datasets select their own CRS; this constant is never mutated.
WGS84_CRS = "EPSG:4326"
WORKING_CRS = "EPSG:32643"


@lru_cache(maxsize=128)
def _transformer(source_crs: str, target_crs: str):
    return Transformer.from_crs(source_crs, target_crs, always_xy=True)


def wgs84_to_working(lon: float, lat: float, working_crs: str = WORKING_CRS) -> tuple:
    """Project longitude/latitude; pipeline callers must supply their context CRS."""
    return _transformer(WGS84_CRS, working_crs).transform(lon, lat)


def working_to_wgs84(x: float, y: float, working_crs: str = WORKING_CRS) -> tuple:
    """Export longitude/latitude using the source graph's working CRS."""
    return _transformer(working_crs, WGS84_CRS).transform(x, y)


def _reproject_geom(geom, working_crs: str):
    """Reproject a WGS84 geometry into the dataset's selected working CRS."""
    transformer = _transformer(WGS84_CRS, working_crs)
    return shapely_transform(lambda x, y, z=None: transformer.transform(x, y), geom)


def _select_working_crs(geometries) -> str:
    """Choose one WGS84 UTM zone from the geographic bounding-box midpoint.

    Intended for local datasets, not antimeridian-crossing or regional mosaics.
    Empty datasets retain the legacy demo default because they have no location.
    """
    bounds = [g.bounds for g in geometries if not g.is_empty]
    if not bounds:
        return WORKING_CRS
    if any(not all(math.isfinite(v) for v in b) or
           b[0] < -180 or b[2] > 180 or b[1] < -80 or b[3] > 84
           for b in bounds):
        raise ValueError("GIS coordinates must be WGS84 longitude/latitude within UTM coverage (-80 to 84 latitude)")
    west, east = min(b[0] for b in bounds), max(b[2] for b in bounds)
    if east - west > 180:
        raise ValueError("Antimeridian-spanning GIS data requires an explicit projection strategy")
    lon = (west + east) / 2
    lat = (min(b[1] for b in bounds) + max(b[3] for b in bounds)) / 2
    zone = min(60, int((lon + 180) // 6) + 1)
    # Standard UTM zone exceptions for Norway and Svalbard.
    if 56 <= lat < 64 and 3 <= lon < 12:
        zone = 32
    elif 72 <= lat < 84 and 0 <= lon < 42:
        zone = 31 if lon < 9 else 33 if lon < 21 else 35 if lon < 33 else 37
    return f"EPSG:{(32600 if lat >= 0 else 32700) + zone}"


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class InfraPoint:
    id: str
    node_type: str          # power | road | hospital | telecom | water | social
    name: str
    geometry: Point         # in GISData.working_crs
    capacity: Optional[float] = None   # None => caller must apply a default lookup


@dataclass
class RoadSegment:
    id: str
    name: str
    geometry: LineString    # in GISData.working_crs
    lanes: Optional[int] = None


@dataclass
class Building:
    id: str
    name: str
    geometry: Polygon       # in GISData.working_crs


@dataclass
class GISData:
    source_label: str                       # e.g. "DEMO_PLACEHOLDER_FIXTURE"
    infra_points: list = field(default_factory=list)   # list[InfraPoint]
    road_segments: list = field(default_factory=list)  # list[RoadSegment]
    buildings: list = field(default_factory=list)       # list[Building]
    excluded_infra_count: int = 0
    working_crs: str = WORKING_CRS

    def is_demo_fixture(self) -> bool:
        return self.source_label == "DEMO_PLACEHOLDER_FIXTURE"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def load_gis(path: str) -> GISData:
    """
    Load a GeoJSON GIS source and return a GISData object with every
    geometry reprojected into an automatically selected local UTM CRS,
    carried in GISData.working_crs. Input coordinates must be WGS84/CRS84
    longitude/latitude. One zone is selected for the whole dataset.

    Expected feature `properties.feature_class` values:
        "infrastructure" -> InfraPoint  (needs node_type, name, capacity)
        "road_segment"   -> RoadSegment (needs name, lanes)
        "building"       -> Building    (needs building_id, name)

    Infrastructure with missing or unsupported node_type is excluded,
    reported, and counted without inferring a replacement type.
    """
    with open(path, "r") as f:
        raw = json.load(f)

    source_label = raw.get("metadata", {}).get("source", "UNKNOWN_SOURCE")
    if source_label == "DEMO_PLACEHOLDER_FIXTURE":
        print(
            "[gis_loader] WARNING: loading DEMO_PLACEHOLDER_FIXTURE — "
            "this is not real GIS data. Replace with the real dataset "
            "before any non-development use."
        )

    infra_points, road_segments, buildings = [], [], []
    excluded_infra_count = 0
    accepted_features = []

    for i, feat in enumerate(raw["features"]):
        props = feat["properties"]
        fclass = props.get("feature_class")

        # Native SpaceNet8 schema compatibility
        if fclass is None:
            if props.get("highway") is not None:
                fclass = "road_segment"
            elif props.get("building") == "yes":
                fclass = "building"
        if fclass == "infrastructure":
            node_type = props.get("node_type")
            if not isinstance(node_type, str) or node_type not in VALID_NODE_TYPES:
                excluded_infra_count += 1
                print(
                    f"[gis_loader] Excluding infrastructure feature {i}: "
                    f"unsupported or missing node_type={node_type!r}"
                )
                continue
        if fclass not in {"infrastructure", "road_segment", "building"}:
            print(f"[gis_loader] Skipping feature with unknown feature_class: {fclass}")
            continue
        accepted_features.append((i, props, fclass, shape(feat["geometry"])))

    working_crs = _select_working_crs(item[3] for item in accepted_features)
    for i, props, fclass, geom in accepted_features:
        geom_working = _reproject_geom(geom, working_crs)

        if fclass == "infrastructure":
            infra_points.append(InfraPoint(
                id=str(props.get("infrastructure_id", f"infra_{i}")),
                node_type=props["node_type"],
                name=props.get("name", f"infra_{i}"),
                geometry=geom_working,
                capacity=props.get("capacity"),
            ))
        elif fclass == "road_segment":
            road_segments.append(RoadSegment(
                id=str(props.get("road_id", f"road_{i}")),
                name=props.get("name", f"road_{i}"),
                geometry=geom_working,
                lanes=props.get("lanes"),
            ))
        elif fclass == "building":
            buildings.append(Building(
                id=props.get("building_id", f"bldg_{i}"),
                name=props.get("name", f"bldg_{i}"),
                geometry=geom_working,
            ))

    return GISData(
        source_label=source_label,
        infra_points=infra_points,
        road_segments=road_segments,
        buildings=buildings,
        excluded_infra_count=excluded_infra_count,
        working_crs=working_crs,
    )


if __name__ == "__main__":
    # Quick manual check
    data = load_gis("gis/fixtures/demo_gis.geojson")
    print(f"source: {data.source_label}")
    print(f"infra points: {len(data.infra_points)}")
    print(f"road segments: {len(data.road_segments)}")
    print(f"buildings: {len(data.buildings)}")
