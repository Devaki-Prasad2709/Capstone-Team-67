"""Dashboard GeoJSON for building footprints and manual classifications."""

from __future__ import annotations

from pyproj import Transformer
from shapely.geometry import mapping
from shapely.ops import transform


def build_building_overlay(gis_data, id_map: dict, classification_store) -> dict:
    transformer = Transformer.from_crs(
        gis_data.working_crs, "EPSG:4326", always_xy=True
    )
    features = []
    for building in gis_data.buildings:
        classification = classification_store.get(building.id)
        geographic = transform(transformer.transform, building.geometry)
        features.append(
            {
                "type": "Feature",
                "id": building.id,
                "geometry": mapping(geographic),
                "properties": {
                    "building_source_id": building.id,
                    "name": building.name,
                    "graph_node_id": id_map[building.id],
                    "assigned_type": (
                        classification["assigned_type"] if classification else None
                    ),
                    "classification": classification,
                    "classification_source": (
                        "manual_revision_store" if classification else None
                    ),
                },
            }
        )
    return {"type": "FeatureCollection", "features": features}
