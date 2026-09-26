"""Immutable temporal graph-state updates and dependency propagation.

Every adapter updates a deep-copied snapshot. Node state is derived through the
same formulas here, and dependency edges (provider -> dependent, edge_type=1)
reduce service availability without pretending that physical damage propagated.
"""

from __future__ import annotations

import copy
import math


DAMAGE_CAPACITY_EXPONENT_K = 1.5
MIN_EFFECTIVE_CAPACITY = 1e-6
DEFAULT_STALE_AFTER_SECONDS = 30 * 60
DEGRADED_STRESS_THRESHOLD = 0.75
DEGRADED_DAMAGE_THRESHOLD = 0.30
FAILED_DAMAGE_THRESHOLD = 0.95

STATE_FIELDS = (
    "damage",
    "load",
    "capacity",
    "effective_capacity",
    "utilization",
    "stress",
    "status",
    "status_label",
    "dependency_factor",
    "state_timestamp",
    "last_observation_timestamp",
    "freshness_seconds",
    "freshness_status",
)


def _valid_timestamp(value, field="timestamp") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{field} must be a finite epoch number")
    return float(value)


def _freshness(last_observation_timestamp, now, stale_after_seconds):
    if last_observation_timestamp is None:
        return None, "missing"
    age = max(0.0, now - float(last_observation_timestamp))
    return age, "stale" if age > stale_after_seconds else "current"


def update_node_freshness(
    node: dict,
    now: float,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
) -> None:
    age, freshness = _freshness(
        node.get("last_observation_timestamp"), now, stale_after_seconds
    )
    node["freshness_seconds"] = age
    node["freshness_status"] = freshness


def recompute_node_state(node: dict, *, timestamp: float | None = None) -> None:
    """Recalculate all derived state fields in place from primary node values."""
    capacity = float(node["capacity"])
    load = float(node["load"])
    damage = float(node["damage"])
    if capacity <= 0 or load < 0 or not 0 <= damage <= 1:
        raise ValueError("Node capacity/load/damage are outside valid ranges")
    dependency_factor = float(node.get("dependency_factor", 1.0))
    dependency_factor = min(1.0, max(0.0, dependency_factor))
    intrinsic = capacity * (1.0 - damage) ** DAMAGE_CAPACITY_EXPONENT_K
    effective = max(intrinsic * dependency_factor, MIN_EFFECTIVE_CAPACITY)
    utilization = load / capacity
    stress = min(1.0, load / effective)

    if (
        damage >= FAILED_DAMAGE_THRESHOLD
        or stress >= 1.0 - 1e-12
        or dependency_factor <= 0.01
    ):
        status, status_label = 0, "failed"
    elif (
        damage >= DEGRADED_DAMAGE_THRESHOLD
        or stress >= DEGRADED_STRESS_THRESHOLD
        or dependency_factor < 1.0 - 1e-12
    ):
        status, status_label = 1, "degraded"
    else:
        status, status_label = 1, "operational"

    node.update(
        {
            "effective_capacity": effective,
            "utilization": utilization,
            "stress": stress,
            "status": status,
            "status_label": status_label,
            "dependency_factor": dependency_factor,
        }
    )
    if timestamp is not None:
        node["state_timestamp"] = _valid_timestamp(timestamp, "state timestamp")


def propagate_dependency_failures(graph, *, timestamp: float | None = None):
    """Return a snapshot with provider availability propagated to dependents.

    Dependency failure reduces dependent effective capacity and can therefore
    raise stress/status. It never copies provider damage onto a dependent node.
    Fixed-point iteration supports chains such as power -> telecom -> social.
    """
    updated = copy.deepcopy(graph)
    dependency_nodes = {
        target
        for source, target, edge in updated.edges(data=True)
        if edge.get("edge_type") == 1
    }
    for node_id in updated.nodes:
        updated.nodes[node_id]["dependency_factor"] = 1.0
        recompute_node_state(updated.nodes[node_id], timestamp=timestamp)

    for _ in range(max(1, len(updated))):
        changed = False
        for target in dependency_nodes:
            providers = [
                (source, edge)
                for source, _, edge in updated.in_edges(target, data=True)
                if edge.get("edge_type") == 1
            ]
            if not providers:
                continue
            availability = []
            for source, edge in providers:
                provider = updated.nodes[source]
                base_capacity = max(float(provider["capacity"]), MIN_EFFECTIVE_CAPACITY)
                available = min(1.0, max(0.0, provider["effective_capacity"] / base_capacity))
                if provider["status_label"] == "failed":
                    available = 0.0
                weight = float(edge.get("weight", 1.0))
                availability.append(1.0 - min(1.0, max(0.0, weight)) * (1.0 - available))
            factor = min(availability)
            node = updated.nodes[target]
            if not math.isclose(float(node.get("dependency_factor", 1.0)), factor, abs_tol=1e-12):
                node["dependency_factor"] = factor
                recompute_node_state(node, timestamp=timestamp)
                changed = True
        if not changed:
            break
    return updated


def apply_observations(
    graph,
    observation_log,
    now: float | None = None,
    *,
    stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
):
    """Return G(t) with accepted visual observations applied and cascaded."""
    if now is None:
        import time

        now = time.time()
    now = _valid_timestamp(now, "snapshot timestamp")
    previous_snapshot = graph.graph.get("snapshot_timestamp")
    if previous_snapshot is not None and now < float(previous_snapshot):
        raise ValueError("Stale observation snapshot cannot overwrite newer graph state")
    if stale_after_seconds < 0 or not math.isfinite(stale_after_seconds):
        raise ValueError("stale_after_seconds must be a finite non-negative number")
    updated = copy.deepcopy(graph)
    updated.graph["snapshot_timestamp"] = now
    updated.graph["snapshot_sequence"] = int(graph.graph.get("snapshot_sequence", 0)) + 1

    observed_nodes = set(observation_log.all_observed_node_ids())
    for node_id in updated.nodes:
        node = updated.nodes[node_id]
        if node_id in observed_nodes:
            newest = observation_log.latest_timestamp(node_id, at_or_before=now)
            if newest is not None:
                observed_damage = observation_log.aggregate_damage(node_id, now=now)
                node["damage"] = max(float(node.get("damage", 0.0)), observed_damage)
                prior = node.get("last_observation_timestamp")
                node["last_observation_timestamp"] = max(
                    value for value in (prior, newest) if value is not None
                )
        update_node_freshness(node, now, stale_after_seconds)
        node["dependency_factor"] = 1.0
        recompute_node_state(node, timestamp=now)

    return propagate_dependency_failures(updated, timestamp=now)


def explain_graph_transition(before, after, *, event_id: str, event_type: str) -> dict:
    """Produce a JSON-ready before/after audit for one scenario event."""
    before_nodes, after_nodes = set(before.nodes), set(after.nodes)
    topology_changed = before_nodes != after_nodes or set(before.edges) != set(after.edges)
    changes = []
    for node_id in sorted(before_nodes & after_nodes):
        previous, current = before.nodes[node_id], after.nodes[node_id]
        fields = {}
        for field in STATE_FIELDS:
            old, new = previous.get(field), current.get(field)
            if old != new:
                entry = {"before": old, "after": new}
                if (
                    isinstance(old, (int, float))
                    and not isinstance(old, bool)
                    and isinstance(new, (int, float))
                    and not isinstance(new, bool)
                ):
                    entry["delta"] = new - old
                fields[field] = entry
        if fields:
            changes.append(
                {
                    "graph_node_id": node_id,
                    "gis_source_id": current.get("gis_source_id"),
                    "changes": fields,
                }
            )
    changed_fields = sorted(
        {field for change in changes for field in change["changes"]}
    )
    if topology_changed:
        explanation = "Graph topology changed; inspect node/edge sets before accepting this transition."
    elif changes:
        explanation = (
            f"{len(changes)} node(s) changed fields: {', '.join(changed_fields)}."
        )
    else:
        explanation = "No graph topology or node-state fields changed for this event."
    return {
        "event_id": event_id,
        "event_type": event_type,
        "graph_changed": bool(changes or topology_changed),
        "topology_changed": topology_changed,
        "changed_node_count": len(changes),
        "node_changes": changes,
        "changed_fields": changed_fields,
        "explanation": explanation,
        "before_snapshot_timestamp": before.graph.get("snapshot_timestamp"),
        "after_snapshot_timestamp": after.graph.get("snapshot_timestamp"),
    }
