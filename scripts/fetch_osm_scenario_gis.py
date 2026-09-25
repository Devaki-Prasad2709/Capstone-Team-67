"""Fetch a bounded OpenStreetMap snapshot into the project's GIS contract."""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ENDPOINTS = (
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
)
USER_AGENT = "disaster-streaming-capstone/1.0"


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    values = tuple(float(item.strip()) for item in raw.split(","))
    if len(values) != 4:
        raise ValueError("bbox must be west,south,east,north")
    west, south, east, north = values
    if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError("bbox must contain ordered WGS84 coordinates")
    return west, south, east, north


def core_query(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    box = f"({south},{west},{north},{east})"
    return (
        "[out:json][timeout:90];("
        f'way["highway"]{box};'
        f'way["building"]{box};'
        ");out geom tags;"
    )


def infrastructure_query(bbox: tuple[float, float, float, float]) -> str:
    west, south, east, north = bbox
    box = f"({south},{west},{north},{east})"
    return (
        "[out:json][timeout:90];("
        f'node["amenity"="hospital"]{box};'
        f'node["amenity"="clinic"]{box};'
        f'node["power"="tower"]{box};'
        f'node["power"="pole"]{box};'
        f'node["power"="transformer"]{box};'
        f'node["man_made"="water_tower"]{box};'
        f'node["man_made"="water_works"]{box};'
        f'node["man_made"="communications_tower"]{box};'
        ");out tags;"
    )


def fetch_overpass(query: str) -> dict[str, Any]:
    error: Exception | None = None
    for endpoint in ENDPOINTS:
        url = f"{endpoint}?{urllib.parse.urlencode({'data': query})}"
        request = urllib.request.Request(
            url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                return json.load(response)
        except Exception as exc:  # try the next public mirror
            error = exc
            time.sleep(1)
    raise ConnectionError(f"All Overpass endpoints failed: {error}")


def _point(element: dict[str, Any]) -> list[float] | None:
    if "lon" in element and "lat" in element:
        return [float(element["lon"]), float(element["lat"])]
    center = element.get("center") or {}
    if "lon" in center and "lat" in center:
        return [float(center["lon"]), float(center["lat"])]
    geometry = element.get("geometry") or []
    if geometry:
        return [
            sum(float(item["lon"]) for item in geometry) / len(geometry),
            sum(float(item["lat"]) for item in geometry) / len(geometry),
        ]
    return None


def _line(element: dict[str, Any]) -> list[list[float]]:
    return [
        [float(item["lon"]), float(item["lat"])]
        for item in element.get("geometry") or []
    ]


def _integer_lanes(value: Any) -> int | None:
    try:
        lanes = int(str(value).split(";")[0])
    except (TypeError, ValueError):
        return None
    return lanes if lanes > 0 else None


def convert(payload: dict[str, Any], bbox: tuple[float, float, float, float]) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    seen: set[tuple[str, int, str]] = set()
    elements = sorted(payload.get("elements", []), key=lambda item: (item.get("type", ""), item.get("id", 0)))

    for element in elements:
        tags = element.get("tags") or {}
        osm_type = str(element.get("type", "unknown"))
        osm_id = int(element.get("id", 0))
        source_id = f"osm-{osm_type}-{osm_id}"

        if element.get("type") == "way" and tags.get("building"):
            coordinates = _line(element)
            if len(coordinates) < 3:
                continue
            if coordinates[0] != coordinates[-1]:
                coordinates.append(coordinates[0])
            kind = "building"
            geometry = {"type": "Polygon", "coordinates": [coordinates]}
            properties = {
                "feature_class": "building",
                "building_id": source_id,
                "name": tags.get("name", source_id),
                "building": tags.get("building"),
            }
        elif element.get("type") == "way" and tags.get("highway"):
            coordinates = _line(element)
            if len(coordinates) < 2:
                continue
            kind = "road_segment"
            geometry = {"type": "LineString", "coordinates": coordinates}
            properties = {
                "feature_class": "road_segment",
                "road_id": source_id,
                "name": tags.get("name", source_id),
                "highway": tags.get("highway"),
                "lanes": _integer_lanes(tags.get("lanes")),
            }
        else:
            amenity, power, man_made = tags.get("amenity"), tags.get("power"), tags.get("man_made")
            if amenity in {"hospital", "clinic"}:
                node_type = "hospital"
            elif power and power not in {"line", "minor_line", "cable"}:
                node_type = "power"
            elif man_made in {"water_tower", "water_works"}:
                node_type = "water"
            elif man_made == "communications_tower":
                node_type = "telecom"
            else:
                continue
            coordinates = _point(element)
            if coordinates is None:
                continue
            kind = "infrastructure"
            geometry = {"type": "Point", "coordinates": coordinates}
            properties = {
                "feature_class": "infrastructure",
                "infrastructure_id": source_id,
                "node_type": node_type,
                "name": tags.get("name", source_id),
                "capacity": None,
            }

        key = (osm_type, osm_id, kind)
        if key in seen:
            continue
        seen.add(key)
        properties.update({"osm_type": osm_type, "osm_id": osm_id, "source": "OpenStreetMap"})
        features.append({"type": "Feature", "geometry": geometry, "properties": properties})

    counts: dict[str, int] = {}
    for feature in features:
        key = feature["properties"]["feature_class"]
        counts[key] = counts.get(key, 0) + 1
    return {
        "type": "FeatureCollection",
        "metadata": {
            "source": "OPENSTREETMAP_OVERPASS_SNAPSHOT",
            "license": "Open Database License (ODbL) 1.0",
            "attribution": "© OpenStreetMap contributors",
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "bbox": list(bbox),
            "crs": "EPSG:4326",
            "feature_counts": counts,
        },
        "features": features,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bbox", required=True, help="west,south,east,north")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    bbox = parse_bbox(args.bbox)
    # Keeping geometry-heavy roads/buildings separate from infrastructure avoids
    # timeouts on public Overpass instances and makes retries much cheaper.
    core = fetch_overpass(core_query(bbox))
    infrastructure = fetch_overpass(infrastructure_query(bbox))
    result = convert(
        {"elements": core.get("elements", []) + infrastructure.get("elements", [])},
        bbox,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["metadata"], indent=2))
    print(f"Wrote {len(result['features'])} features to {args.output}")


if __name__ == "__main__":
    main()
