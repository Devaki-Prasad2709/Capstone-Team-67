"""
gis_graph_builder.py

GIS-driven replacement for the synthetic `build_graph()` in the original
graph_builder.py. Produces a NetworkX DiGraph with the EXACT SAME node/edge
attribute contract as the original, so `helpers.nx_to_pyg` and `TGNN` need
no changes:

    Node attrs: type, pos, load, capacity, damage, stress, utilization,
                threshold, resilience, status
    Edge attrs: weight, delay, edge_type   (0 = spatial, 1 = dependency)

What changed vs. the original random generator:
  - Nodes come from GIS infrastructure points, road segment midpoints,
    and building centroids (provisional social nodes).
  - `pos` is now the WORKING CRS (x, y) in meters, not [0,1] random floats.
    (Reprojected back to WGS84 only at the overlay/export boundary --
    see overlay/gis_overlay.py.)
  - Spatial edges connect nodes within a real distance radius (meters),
    not an arbitrary [0,1]-space threshold.
  - Dependency edges connect each dependent node to its NEAREST provider(s)
    of the required type (per the locked architecture rule: "nearest
    provider within a service radius", not the original's all-pairs
    same-type connection, which doesn't make physical sense at real scale).
  - `load`, `capacity`, `damage`, `stress` are baseline/default values at
    G(0) construction time -- they get overwritten by real observations
    via observation/state_update.py before each TGNN call. This file only
    establishes topology + defensible baselines, exactly as scoped in the
    Final Architecture (GIS = structure, Observation Log = state).

Everything here is disclosed and tunable -- see the constants below.
"""

import math
import random
import networkx as nx

from core.gis.gis_loader import GISData

NODE_TYPES = ["power", "road", "hospital", "telecom", "water", "social"]

# Which types each type depends on (provider types), same semantics as the
# original graph_builder.py's DEPENDENCIES dict (kept identical on purpose
# so nothing downstream needs to change).
DEPENDENCIES = {
    "hospital": ["power", "road"],
    "telecom": ["power"],
    "water": ["power"],
    "social": ["telecom"],
    "road": [],
    "power": [],
}

# Disclosed, tunable constants -- these are assumptions, not measurements.
SPATIAL_EDGE_RADIUS_M = 400.0       # nodes within this distance get a spatial edge
DEPENDENCY_SERVICE_RADIUS_M = 1200.0  # max distance to look for a dependency provider

# Baseline defaults used ONLY when GIS doesn't supply a value.
# These are engineering-default placeholders, disclosed here rather than
# silently assumed. Replace with real capacity data / lookup tables (e.g.
# per road class, per substation rating) as soon as it's available.
DEFAULT_CAPACITY_BY_TYPE = {
    "power": 1.0,
    "road": 0.8,
    "hospital": 0.9,
    "telecom": 0.8,
    "water": 0.75,
    "social": 0.6,
}

# Fraction of capacity assumed as baseline load with no observations yet.
# This is a modeling assumption (disclosed), not a measured quantity.
BASELINE_LOAD_FRACTION = 0.35


def _node_positions(G):
    return {n: G.nodes[n]["pos"] for n in G.nodes}


def build_graph_from_gis(gis_data: GISData, seed: int = None):
    """
    Build G(0): topology + baseline features, from real GIS data.
    Call this once per GIS ingestion (topology is static); call
    observation/state_update.py on the result every tick to get G(t).

    Returns (G, id_map):
        G       -- NetworkX DiGraph with INTEGER node keys (0..N-1), matching
                   the existing nx_to_pyg contract exactly (it builds
                   edge_index from node keys directly, so they must be
                   integers matching row order -- this is an existing-file
                   constraint we deliberately preserve rather than touch).
        id_map  -- {gis_element_id (str, e.g. "road_8"): integer node_id}.
                   Pass this to building_association.build_building_lookup()
                   so building/road/provider lookups resolve to the SAME
                   integer ids the graph and PyG tensors use -- this is the
                   one place that translation happens, so nothing downstream
                   needs to know GIS ids exist at all.
    """
    if seed is not None:
        random.seed(seed)

    G = nx.DiGraph(
        working_crs=gis_data.working_crs,
        snapshot_timestamp=None,
        snapshot_sequence=0,
    )
    id_map = {}
    node_id = 0

    # --- Infrastructure points become nodes directly ---
    for p in gis_data.infra_points:
        capacity = p.capacity if p.capacity is not None else DEFAULT_CAPACITY_BY_TYPE[p.node_type]
        _add_node(
            G, node_id, node_type=p.node_type, pos=(p.geometry.x, p.geometry.y),
            capacity=capacity, gis_source_id=p.id,
        )
        id_map[p.id] = node_id
        node_id += 1

    # --- One road node per segment, placed at its midpoint ---
    for r in gis_data.road_segments:
        mid = r.geometry.interpolate(0.5, normalized=True)
        capacity = DEFAULT_CAPACITY_BY_TYPE["road"]
        try:
            lanes = float(r.lanes) if not isinstance(r.lanes, bool) else 0.0
        except (TypeError, ValueError, OverflowError):
            lanes = 0.0
        if math.isfinite(lanes) and lanes > 0:
            # more lanes -> proportionally higher capacity; disclosed heuristic
            capacity = min(1.0, DEFAULT_CAPACITY_BY_TYPE["road"] * (lanes / 2.0))
        _add_node(
            G, node_id, node_type="road", pos=(mid.x, mid.y), capacity=capacity,
            gis_source_id=r.id,
        )
        id_map[r.id] = node_id
        node_id += 1

    # Buildings have no verified infrastructure type or capacity. Represent
    # their footprints provisionally as social nodes using existing defaults.
    for b in gis_data.buildings:
        centroid = b.geometry.centroid
        _add_node(
            G, node_id, node_type="social", pos=(centroid.x, centroid.y),
            capacity=DEFAULT_CAPACITY_BY_TYPE["social"], gis_source_id=b.id,
        )
        id_map[b.id] = node_id
        node_id += 1

    _add_spatial_edges(G)
    _add_dependency_edges(G)

    return G, id_map


def _add_node(G, node_id, node_type, pos, capacity, gis_source_id=None):
    load = capacity * BASELINE_LOAD_FRACTION
    damage = 0.0
    effective_capacity = capacity  # no damage yet at G(0)
    stress = min(1.0, load / effective_capacity) if effective_capacity > 0 else 0.0

    G.add_node(
        node_id,
        gis_source_id=gis_source_id,
        type=node_type,
        pos=pos,
        load=load,
        capacity=capacity,
        damage=damage,
        effective_capacity=effective_capacity,
        stress=stress,
        utilization=load / capacity if capacity > 0 else 0.0,   # kept for compatibility;
        threshold=1.4,                                           # not consumed by nx_to_pyg
        resilience=1.0,                                          # or by TGNN (see helpers.py)
        status=1,
        status_label="operational",
        dependency_factor=1.0,
        state_timestamp=None,
        last_observation_timestamp=None,
        freshness_seconds=None,
        freshness_status="missing",
    )


def _dist(a, b):
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


def _add_spatial_edges(G):
    """
    Spatial edges = shared physical exposure (co-location), per the locked
    edge-justification rule. Undirected in meaning, represented here as a
    pair of directed edges (matches the original graph's DiGraph convention).
    """
    positions = _node_positions(G)
    nodes = list(G.nodes)
    for i_idx, i in enumerate(nodes):
        for j in nodes[i_idx + 1:]:
            d = _dist(positions[i], positions[j])
            if d <= SPATIAL_EDGE_RADIUS_M:
                w = max(0.0, 1.0 - d / SPATIAL_EDGE_RADIUS_M)
                G.add_edge(i, j, weight=w, delay=random.randint(1, 3), edge_type=0)
                G.add_edge(j, i, weight=w, delay=random.randint(1, 3), edge_type=0)


def _add_dependency_edges(G):
    """
    Dependency edges = functional physical interdependency, per the locked
    edge-justification rule. Directed: provider -> dependent. Each dependent
    node connects to its NEAREST provider(s) of each required type within
    DEPENDENCY_SERVICE_RADIUS_M -- not all same-type nodes, which is the
    key correction from the original synthetic generator (physically
    implausible at real scale).
    """
    positions = _node_positions(G)
    by_type = {t: [] for t in NODE_TYPES}
    for n, data in G.nodes(data=True):
        by_type[data["type"]].append(n)

    for dependent_type, provider_types in DEPENDENCIES.items():
        if not provider_types:
            continue
        dependent_nodes = by_type[dependent_type]
        for dep_node in dependent_nodes:
            for provider_type in provider_types:
                candidates = by_type[provider_type]
                if not candidates:
                    continue
                best, best_dist = None, float("inf")
                for cand in candidates:
                    d = _dist(positions[dep_node], positions[cand])
                    if d < best_dist:
                        best, best_dist = cand, d
                if best is not None and best_dist <= DEPENDENCY_SERVICE_RADIUS_M:
                    G.add_edge(
                        best, dep_node,
                        weight=1.0,       # placeholder -- see note below
                        delay=random.randint(1, 5),
                        edge_type=1,
                    )
                # NOTE ON `weight` FOR DEPENDENCY EDGES:
                # A dependency edge's weight should ideally reflect something
                # like "fraction of dependent's demand served by this
                # provider," which needs real utility service-area data we
                # don't have yet. Using a flat 1.0 is a disclosed
                # simplification -- do not read this as a measured quantity.


if __name__ == "__main__":
    from gis.gis_loader import load_gis

    data = load_gis("gis/fixtures/demo_gis.geojson")
    G, id_map = build_graph_from_gis(data, seed=42)
    print(f"Nodes: {G.number_of_nodes()}  Edges: {G.number_of_edges()}")
    for n, d in G.nodes(data=True):
        print(n, d["type"], "damage=", round(d["damage"], 2), "stress=", round(d["stress"], 2))
