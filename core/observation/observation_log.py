"""
observation_log.py

The bridge between CV and the graph (locked Final Architecture, section 10):

    raw YOLO detection -> observation -> node association -> decay
    aggregation -> node damage

Raw frame-level detections never become direct TGNN inputs. This module is
what enforces that: it only ever exposes an AGGREGATED damage value per
node, never a raw detection.

Uses the classification framework already agreed:
    Observed:  class, confidence, bbox, position
    Derived:   damage_observed (this module's output)
"""

import math
import time
from dataclasses import dataclass, field
from typing import Optional


# Severity lookup for the CURRENT YOLO classes (Slight/Severe/Debris).
# Disclosed, tunable constant -- not a measured quantity. Update this
# mapping if/when YOLO's class list changes (e.g. adds "Person" -- which
# would NOT go through this severity map at all; person detections feed
# the priority/human-evidence path, not the damage path).
SEVERITY_MAP = {
    "Slight": 0.3,
    "Severe": 0.7,
    "Debris": 1.0,
}

# Decay time constant (seconds). Disclosed assumption -- tune based on how
# fast you want old damage evidence to lose weight relative to fresh
# confirmation. Structural damage itself doesn't self-heal, so this decay
# mainly matters for how much a *stale* observation counts relative to a
# fresh one when multiple observations disagree, not for making damage
# "go away" over time.
DEFAULT_DECAY_TAU_SECONDS = 6 * 3600  # 6 hours


@dataclass
class ObservationRecord:
    observation_id: str
    timestamp: float           # unix epoch seconds, for decay math
    frame_id: str
    track_id: Optional[str]

    source: str                 # "drone"
    class_id: int
    class_name: str
    confidence: float

    bbox: tuple

    latitude: float
    longitude: float
    working_x: float
    working_y: float

    building_id: Optional[str]
    node_id: Optional[int]      # None if it couldn't be associated to any node
    image_reference: Optional[dict] = None
    scenario_timestamp: Optional[str] = None
    location_provenance: Optional[str] = None
    declared_target_id: Optional[str] = None
    target_association_provenance: Optional[str] = None
    matched_gis_source_id: Optional[str] = None
    association_kind: Optional[str] = None
    association_distance_m: Optional[float] = None
    source_content_hash: Optional[str] = None


class ObservationLog:
    """
    Idempotent append-only log, indexed by observation_id and node_id.
    Replays of an existing observation_id are ignored. In-memory for now -- swap the storage backend
    (e.g. a real DB/table) without changing the aggregation logic below,
    since `add()` and `aggregate_damage()` are the only two methods the
    rest of the pipeline calls.
    """

    def __init__(self):
        self._by_node: dict[int, list[ObservationRecord]] = {}
        self._all: list[ObservationRecord] = []
        self._by_id: dict[str, ObservationRecord] = {}

    def add(self, record: ObservationRecord):
        """Append once; return False for an already-seen observation ID."""
        if record.observation_id in self._by_id:
            return False
        self._by_id[record.observation_id] = record
        self._all.append(record)
        if record.node_id is not None:
            self._by_node.setdefault(record.node_id, []).append(record)
        return True

    def get(self, observation_id: str) -> Optional[ObservationRecord]:
        return self._by_id.get(observation_id)

    def __len__(self) -> int:
        return len(self._all)

    def observations_for_node(self, node_id: int) -> list:
        return list(self._by_node.get(node_id, []))

    def all_observed_node_ids(self) -> list:
        return list(self._by_node.keys())

    def latest_timestamp(self, node_id: int, *, at_or_before: float | None = None):
        """Newest accepted observation time, optionally excluding future data."""
        timestamps = [
            record.timestamp
            for record in self._by_node.get(node_id, [])
            if at_or_before is None or record.timestamp <= at_or_before
        ]
        return max(timestamps) if timestamps else None

    def aggregate_damage(self, node_id: int, now: float = None, tau: float = DEFAULT_DECAY_TAU_SECONDS) -> float:
        """
        damage_observed(node, t) = max over observations i of node, t_i <= t:
            severity(class_i) * confidence_i * decay(t - t_i)

        MAX rather than average/sum -- per the locked design decision: one
        confirmed severe sighting should not be diluted by earlier
        "nothing seen" frames. Returns 0.0 if no observations exist yet for
        this node (i.e. GIS baseline damage, not a claim that the node is
        undamaged for certain).
        """
        if now is None:
            now = time.time()

        records = self._by_node.get(node_id, [])
        if not records:
            return 0.0

        best = 0.0
        for r in records:
            if r.timestamp > now:
                continue
            severity = SEVERITY_MAP.get(r.class_name, 0.0)
            decay = math.exp(-(now - r.timestamp) / tau)
            value = severity * r.confidence * decay
            if value > best:
                best = value
        return min(1.0, best)
