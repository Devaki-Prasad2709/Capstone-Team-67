"""Validated infrastructure telemetry contract and graph-state adapter."""

from __future__ import annotations

import copy
import math

from core.observation.state_update import (
    propagate_dependency_failures,
    recompute_node_state,
    update_node_freshness,
)


REQUIRED_FIELDS = ("id", "timestamp", "target_id", "load", "capacity", "damage")


def validate_telemetry_event(event: dict) -> None:
    if not isinstance(event, dict):
        raise ValueError("Telemetry event must be an object")
    for field in REQUIRED_FIELDS:
        if field not in event:
            raise ValueError(f"Telemetry event requires {field}")
    if not isinstance(event["id"], str) or not event["id"]:
        raise ValueError("Telemetry id must be a nonempty string")
    if not isinstance(event["target_id"], str) or not event["target_id"]:
        raise ValueError("Telemetry target_id must be a nonempty GIS ID")
    for field in ("timestamp", "load", "capacity", "damage"):
        value = event[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError(f"Telemetry {field} must be a finite number")
    if event["load"] < 0 or event["capacity"] <= 0:
        raise ValueError("Telemetry load must be non-negative and capacity positive")
    if not 0 <= event["damage"] <= 1:
        raise ValueError("Telemetry damage must be between 0 and 1")
    if "event_type" in event and (
        not isinstance(event["event_type"], str) or not event["event_type"].strip()
    ):
        raise ValueError("Telemetry event_type must be a nonempty string when provided")


def apply_telemetry_event(graph, id_map: dict[str, int], event: dict):
    """Return a new graph with one telemetry event applied to its GIS node.

    Ordinary degradation cannot reduce prior damage. Explicit recovery or
    restoration events may lower measured damage as well as load. Stale and
    conflicting same-time telemetry are rejected rather than silently applied.
    """
    validate_telemetry_event(event)
    if event["target_id"] not in id_map:
        raise KeyError(f"Unknown telemetry target_id: {event['target_id']}")
    node_id = id_map[event["target_id"]]
    if node_id not in graph:
        raise KeyError(f"Telemetry target maps to missing graph node: {node_id}")
    existing = graph.nodes[node_id]
    event_timestamp = float(event["timestamp"])
    snapshot_timestamp = graph.graph.get("snapshot_timestamp")
    if snapshot_timestamp is not None and event_timestamp < float(snapshot_timestamp):
        raise ValueError("Stale telemetry cannot overwrite newer graph state")
    previous_timestamp = existing.get("telemetry_timestamp")
    if previous_timestamp is not None:
        if event_timestamp < float(previous_timestamp):
            raise ValueError("Stale telemetry cannot overwrite newer graph state")
        if event_timestamp == float(previous_timestamp):
            if existing.get("telemetry_event_id") == event["id"]:
                return copy.deepcopy(graph)
            raise ValueError("Conflicting telemetry events share the same timestamp")

    updated = copy.deepcopy(graph)
    updated.graph["snapshot_timestamp"] = event_timestamp
    updated.graph["snapshot_sequence"] = int(graph.graph.get("snapshot_sequence", 0)) + 1
    node = updated.nodes[node_id]
    node["load"] = float(event["load"])
    node["capacity"] = float(event["capacity"])
    event_type = event.get("event_type", "").strip().lower()
    is_recovery = event_type.endswith("recovery") or event_type in {
        "recovery", "restoration", "repaired",
    }
    node["damage"] = (
        float(event["damage"])
        if is_recovery
        else max(float(node.get("damage", 0.0)), float(event["damage"]))
    )
    node["dependency_factor"] = 1.0
    node["telemetry_timestamp"] = event_timestamp
    node["telemetry_event_id"] = event["id"]
    node["last_observation_timestamp"] = max(
        value
        for value in (node.get("last_observation_timestamp"), event_timestamp)
        if value is not None
    )
    node["freshness_seconds"] = 0.0
    node["freshness_status"] = "current"
    recompute_node_state(node, timestamp=event_timestamp)
    for other_node_id in updated.nodes:
        if other_node_id != node_id:
            update_node_freshness(updated.nodes[other_node_id], event_timestamp)
    return propagate_dependency_failures(updated, timestamp=event_timestamp)
