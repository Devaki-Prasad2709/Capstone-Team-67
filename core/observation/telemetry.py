"""Validated infrastructure telemetry contract and graph-state adapter."""

from __future__ import annotations

import copy
import math

from core.observation.state_update import DAMAGE_CAPACITY_EXPONENT_K


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


def apply_telemetry_event(graph, id_map: dict[str, int], event: dict):
    """Return a new graph with one telemetry event applied to its GIS node.

    Damage follows the existing non-self-healing maximum rule. Load and
    capacity are current measurements, so recovery telemetry may lower load.
    Topology and all unrelated nodes remain unchanged.
    """
    validate_telemetry_event(event)
    if event["target_id"] not in id_map:
        raise KeyError(f"Unknown telemetry target_id: {event['target_id']}")
    node_id = id_map[event["target_id"]]
    if node_id not in graph:
        raise KeyError(f"Telemetry target maps to missing graph node: {node_id}")
    updated = copy.deepcopy(graph)
    node = updated.nodes[node_id]
    node["load"] = float(event["load"])
    node["capacity"] = float(event["capacity"])
    node["damage"] = max(float(node.get("damage", 0.0)), float(event["damage"]))
    effective_capacity = max(
        node["capacity"] * (1.0 - node["damage"]) ** DAMAGE_CAPACITY_EXPONENT_K,
        1e-6,
    )
    node["utilization"] = node["load"] / node["capacity"]
    node["stress"] = min(1.0, node["load"] / effective_capacity)
    node["telemetry_timestamp"] = float(event["timestamp"])
    node["telemetry_event_id"] = event["id"]
    return updated
