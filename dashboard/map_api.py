"""Backend map configuration contract for the disaster-response dashboard."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


DEFAULT_CENTER = [-90.02204402568451, 29.73879868866008]
DEFAULT_ZOOM = 15.0

OSM_RASTER_TILES = [
    "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
]


def _walk_coords(value: Any):
    if (
        isinstance(value, list)
        and len(value) >= 2
        and all(
            isinstance(v, (int, float)) and not isinstance(v, bool)
            for v in value[:2]
        )
    ):
        yield float(value[0]), float(value[1])
        return

    if isinstance(value, list):
        for item in value:
            yield from _walk_coords(item)


def _geojson_extent(path: Path) -> list[float] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None

    coords = []

    for feature in payload.get("features", []):
        geometry = feature.get("geometry") or {}
        coords.extend(_walk_coords(geometry.get("coordinates")))

    if not coords:
        return None

    lons = [point[0] for point in coords]
    lats = [point[1] for point in coords]

    return [
        min(lons),
        min(lats),
        max(lons),
        max(lats),
    ]


def map_config() -> dict[str, Any]:
    """Return the stable backend contract consumed by the dashboard."""

    scenario_path = os.getenv("MAP_SCENARIO_GEOJSON")

    extent = (
        _geojson_extent(Path(scenario_path))
        if scenario_path
        else None
    )

    if extent:
        center = [
            (extent[0] + extent[2]) / 2,
            (extent[1] + extent[3]) / 2,
        ]
    else:
        center = list(DEFAULT_CENTER)

    style_url = os.getenv("MAP_STYLE_URL")

    if style_url:
        basemap: dict[str, Any] = {
            "provider": os.getenv("MAP_PROVIDER", "configured"),
            "style_url": style_url,
        }
    else:
        basemap = {
            "provider": "OpenStreetMap",
            "style": {
                "version": 8,
                "name": "Disaster Response Base Map",
                "sources": {
                    "osm": {
                        "type": "raster",
                        "tiles": OSM_RASTER_TILES,
                        "tileSize": 256,
                        "attribution": "© OpenStreetMap contributors",
                    }
                },
                "layers": [
                    {
                        "id": "osm",
                        "type": "raster",
                        "source": "osm",
                    }
                ],
            },
        }

    return {
        "scenario": {
            "id": "spacenet8-louisiana-east",
            "name": "SpaceNet8 Louisiana-East",
            "center": center,
            "bbox": extent,
            "zoom": DEFAULT_ZOOM,
            "crs": "EPSG:4326",
        },
        "basemap": basemap,
        "interaction": {
            "pan": True,
            "zoom": True,
            "bearing": True,
            "pitch": True,
        },
        "overlay_contract": {
            "crs": "EPSG:4326",
            "format": "GeoJSON",
            "layers": [
                "gis_infrastructure",
                "tgnn_predicted_risk",
                "satellite_change",
                "nlp_confirmed_alerts",
            ],
        },
    }
