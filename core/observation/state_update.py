"""
state_update.py

Applies the Observation Log's aggregated damage into the graph, producing
G(t) from G(t-1). This is the exact "YOLO must contribute to the existing
stress=load+damage TGNN ideology" mechanism, made concrete:

    damage_observed(node)  [from Observation Log, decay-aggregated]
            -> combined with baseline/prior damage via max()
            -> effective_capacity = capacity * (1 - damage)^k
            -> stress = clamp(load / effective_capacity, 0, 1)

This mutates a copy of the graph in place (per node) and returns it as G(t).
It does NOT touch topology (nodes/edges) -- only the four state features
(damage, stress; load and capacity are also exposed for tuning but load
is not currently observation-driven, see note below).

DISCLOSED ASSUMPTION: `k` (the damage->capacity-loss exponent) is a tunable
constant, not a measured quantity. k=1 means damage reduces capacity
linearly; k>1 means partial damage disproportionately reduces functional
capacity (e.g. a single blocked lane can close a two-lane road). Currently
set to 1.5 as a middle-ground default -- revisit once you have any real
before/after capacity data to calibrate against.

NOTE ON `load`: real-time load estimation (e.g. from population/service-area
data reacting to an evolving disaster) is out of scope for the current
integration milestone. `load` stays at its GIS-baseline value from
gis_graph_builder.py unless/until a load-estimation source is added. This
is intentional, not an oversight -- flagged here so it isn't mistaken for
a bug later.
"""

import copy

DAMAGE_CAPACITY_EXPONENT_K = 1.5


def apply_observations(G, observation_log, now: float = None):
    """
    Returns a NEW graph (deep copy) representing G(t): topology unchanged,
    damage/stress updated for every node the Observation Log has evidence
    for. Nodes with no observations keep their previous damage/stress
    (i.e. G(0)'s baseline, or whatever the last tick left them at).
    """
    G_t = copy.deepcopy(G)

    for node_id in observation_log.all_observed_node_ids():
        if node_id not in G_t.nodes:
            # Observation resolved to a node_id that isn't in this graph
            # snapshot (e.g. stale reference after a GIS re-ingestion).
            # Skip rather than silently creating a phantom node.
            continue

        damage_observed = observation_log.aggregate_damage(node_id, now=now)

        prior_damage = G_t.nodes[node_id]["damage"]
        damage = max(prior_damage, damage_observed)   # MAX rule, per design

        capacity = G_t.nodes[node_id]["capacity"]
        effective_capacity = capacity * (1.0 - damage) ** DAMAGE_CAPACITY_EXPONENT_K
        effective_capacity = max(effective_capacity, 1e-6)  # avoid div-by-zero

        load = G_t.nodes[node_id]["load"]
        stress = min(1.0, load / effective_capacity)

        G_t.nodes[node_id]["damage"] = damage
        G_t.nodes[node_id]["stress"] = stress
        G_t.nodes[node_id]["utilization"] = load / capacity if capacity > 0 else 0.0

    return G_t
