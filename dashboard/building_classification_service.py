"""Dashboard-facing persistent building classification service."""

from __future__ import annotations

from pathlib import Path

from config.settings import settings
from core.gis.building_association import SpatialIndex, build_building_lookup
from core.gis.building_classification import (
    BuildingClassificationStore,
    apply_building_classifications,
)
from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.overlay.building_overlay import build_building_overlay


DEFAULT_GIS_PATH = (
    Path(__file__).resolve().parents[1]
    / "scenarios/louisiana_east_flood/gis/infrastructure.geojson"
)


class BuildingClassificationService:
    def __init__(self, gis_path: Path = DEFAULT_GIS_PATH, database_path: Path | None = None, *, clock=None):
        self.gis_path = Path(gis_path)
        self.gis_data = load_gis(str(self.gis_path))
        self.graph, self.id_map = build_graph_from_gis(self.gis_data, seed=42)
        building_ids = {building.id for building in self.gis_data.buildings}
        self.store = BuildingClassificationStore(
            database_path or settings.resolved_building_classification_database_path,
            building_source_ids=building_ids,
            clock=clock,
        )
        lookup = build_building_lookup(self.gis_data, self.id_map, self.store)
        self.spatial_index = SpatialIndex(
            self.gis_data, lookup, self.id_map, self.store
        )

    def list_buildings(self):
        return build_building_overlay(self.gis_data, self.id_map, self.store)

    def get_building(self, source_id: str):
        building = next(
            (item for item in self.gis_data.buildings if item.id == source_id), None
        )
        if building is None:
            raise KeyError(source_id)
        graph = apply_building_classifications(self.graph, self.store)
        node_id = self.id_map[source_id]
        return {
            "building_source_id": source_id,
            "name": building.name,
            "graph_node_id": node_id,
            "graph_node": dict(graph.nodes[node_id]),
            "classification": self.store.get(source_id),
            "revision_history": self.store.history(source_id),
        }

    def classify(self, source_id: str, assigned_type: str, operator: str, notes=None):
        classification = self.store.assign(
            source_id, assigned_type, operator=operator, notes=notes
        )
        return {
            "classification": classification,
            "building": self.get_building(source_id),
        }
