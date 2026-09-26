"""
building_association.py

Implements the deterministic bridge between visual evidence and the graph
(locked Final Architecture, section 6/13):

    Building footprints + infrastructure nodes
        -> one-time association
        -> building_id -> nearby/serving infrastructure

    Runtime: geolocated detection -> point-in-polygon -> building GIS/node ID
             (no building match -> nearest road GIS/node ID within a threshold)

NOTE ON SIMPLIFICATION: "nearest by network distance" (the ideal, per the
architecture doc) requires a routable road graph, which doesn't exist yet
because we don't have real road topology data. This module currently uses
straight-line nearest-node-in-working-CRS as a placeholder for network
distance. This is a disclosed simplification, not a silent one -- swap
`_nearest` for a network-distance implementation once real road topology
(with intersections) is available.
"""

from dataclasses import dataclass
import math
from typing import Optional

from shapely.geometry import Point
from shapely.strtree import STRtree

from core.gis.gis_loader import GISData, InfraPoint, Building


NEAREST_ROAD_FALLBACK_RADIUS_M = 150.0  # detections farther than this from
                                          # any road get no node association


@dataclass
class BuildingAssociation:
    building_id: str
    nearest_road: Optional[str] = None
    nearest_power: Optional[str] = None
    nearest_water: Optional[str] = None
    nearest_telecom: Optional[str] = None
    service_hospital: Optional[str] = None
    classification: Optional[dict] = None


def _nearest(point, candidates: list):
    """
    Straight-line nearest candidate. `candidates` is a list of (id, geometry)
    tuples. Returns (id, distance) or (None, None) if candidates is empty.

    SIMPLIFICATION: straight-line distance, not network distance. See module
    docstring.
    """
    if not candidates:
        return None, None
    best_id, best_dist = None, float("inf")
    for cid, geom in candidates:
        d = point.distance(geom)
        if d < best_dist:
            best_id, best_dist = cid, d
    return best_id, best_dist


def build_building_lookup(gis_data: GISData, id_map: dict, classification_store=None) -> dict:
    """
    One-time association, run once per GIS ingestion (not per detection).

    `id_map` is the {gis_element_id: integer_node_id} mapping returned by
    gis_graph_builder.build_graph_from_gis() -- required so this lookup's
    output (BuildingAssociation.nearest_road etc.) uses the SAME integer
    node ids the graph and PyG tensors use, not GIS-native string ids.

    Returns {building_id: BuildingAssociation}.
    """
    by_type = {"power": [], "road": [], "water": [], "telecom": [], "hospital": []}
    for p in gis_data.infra_points:
        if p.node_type in by_type:
            by_type[p.node_type].append((id_map[p.id], p.geometry))
    for r in gis_data.road_segments:
        by_type["road"].append((id_map[r.id], r.geometry))

    lookup = {}
    for b in gis_data.buildings:
        centroid = b.geometry.centroid
        nearest_road, _ = _nearest(centroid, by_type["road"])
        nearest_power, _ = _nearest(centroid, by_type["power"])
        nearest_water, _ = _nearest(centroid, by_type["water"])
        nearest_telecom, _ = _nearest(centroid, by_type["telecom"])
        service_hospital, _ = _nearest(centroid, by_type["hospital"])

        lookup[b.id] = BuildingAssociation(
            building_id=b.id,
            nearest_road=nearest_road,
            nearest_power=nearest_power,
            nearest_water=nearest_water,
            nearest_telecom=nearest_telecom,
            service_hospital=service_hospital,
            classification=(classification_store.get(b.id) if classification_store else None),
        )
    return lookup


class SpatialIndex:
    """
    Runtime lookup structure: given a geolocated point (in working CRS),
    find which building it falls inside (if any), or fall back to the
    nearest road node within NEAREST_ROAD_FALLBACK_RADIUS_M.

    Built once after `build_building_lookup`; queried once per detection.
    """

    def __init__(
        self, gis_data: GISData, building_lookup: dict, id_map: dict,
        classification_store=None,
    ):
        self.gis_data = gis_data
        self.building_lookup = building_lookup
        self.id_map = dict(id_map)
        self.classification_store = classification_store

        self._building_geoms = [b.geometry for b in gis_data.buildings]
        self._building_ids = [b.id for b in gis_data.buildings]
        self._building_node_ids = [id_map[b.id] for b in gis_data.buildings]
        self._building_tree = STRtree(self._building_geoms) if self._building_geoms else None

        self._road_candidates = [
            (r.id, id_map[r.id], r.geometry)
            for r in gis_data.road_segments
            if r.id in id_map
        ]

    def resolve(self, working_x: float, working_y: float) -> dict:
        """
        Returns a dict describing what this detection should be attached to:
            {"building_id": ..., "node_id": ...}
        Both the stable GIS source ID and integer graph node ID are returned.
        `building_id` is None if the point fell outside every building
        footprint (open ground / mid-road debris case).
        """
        if (
            isinstance(working_x, bool)
            or isinstance(working_y, bool)
            or not isinstance(working_x, (int, float))
            or not isinstance(working_y, (int, float))
            or not math.isfinite(working_x)
            or not math.isfinite(working_y)
        ):
            raise ValueError("Association coordinates must be finite projected numbers")
        point = Point(working_x, working_y)

        if self._building_tree is not None:
            candidate_idxs = self._building_tree.query(point)
            matches = [
                int(idx) for idx in candidate_idxs
                if self._building_geoms[int(idx)].covers(point)
            ]
            if matches:
                # Choose the most specific footprint; GIS source ID breaks
                # equal-area ties deterministically across STRtree versions.
                idx = min(
                    matches,
                    key=lambda item: (
                        self._building_geoms[item].area,
                        self._building_ids[item],
                    ),
                )
                source_id = self._building_ids[idx]
                node_id = self._building_node_ids[idx]
                return {
                    "association_kind": "building",
                    "gis_source_id": source_id,
                    "graph_node_id": node_id,
                    "building_id": source_id,
                    "node_id": node_id,
                    "distance_m": 0.0,
                    "candidate_count": len(matches),
                    "building_classification": (
                        self.classification_store.get(source_id)
                        if self.classification_store else self.building_lookup[source_id].classification
                    ),
                }

        # No building match -> nearest road fallback, within threshold
        roads = sorted(
            (
                (float(point.distance(geometry)), source_id, node_id)
                for source_id, node_id, geometry in self._road_candidates
            ),
            key=lambda item: (item[0], item[1]),
        )
        if roads and roads[0][0] <= NEAREST_ROAD_FALLBACK_RADIUS_M:
            distance, source_id, node_id = roads[0]
            nearest_count = sum(math.isclose(item[0], distance, abs_tol=1e-9) for item in roads)
            return {
                "association_kind": "road",
                "gis_source_id": source_id,
                "graph_node_id": node_id,
                "building_id": None,
                "node_id": node_id,
                "distance_m": distance,
                "candidate_count": nearest_count,
                "building_classification": None,
            }

        return {
            "association_kind": None,
            "gis_source_id": None,
            "graph_node_id": None,
            "building_id": None,
            "node_id": None,
            "distance_m": None,
            "candidate_count": 0,
            "building_classification": None,
        }
