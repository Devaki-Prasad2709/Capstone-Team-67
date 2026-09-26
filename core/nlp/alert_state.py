"""Auditable responder-review state for NLP-derived social alerts.

The explicit state machine is::

    INCOMING -> PENDING_REVIEW -> CONFIRMED
                              -> REPORTED_FALSE

Only confirmation creates a graph-linked social observation and dashboard
hotspot. That evidence never changes structural damage or TGNN features.
"""

from copy import deepcopy
from datetime import datetime, timezone
import math
from threading import RLock

from pyproj import CRS

from core.gis.gis_loader import working_to_wgs84
from core.nlp.landmark_alert_resolver import ResolvedPriorityAlert

INCOMING = "INCOMING"
PENDING_REVIEW = "PENDING_REVIEW"
CONFIRMED = "CONFIRMED"
REPORTED_FALSE = "REPORTED_FALSE"
ALL_STATUSES = (INCOMING, PENDING_REVIEW, CONFIRMED, REPORTED_FALSE)


class InvalidTransition(ValueError):
    """A lifecycle step or terminal decision is invalid for current state."""


class AlertStore:
    """Thread-safe in-memory alert, audit-history and graph-observation store."""

    def __init__(self, graph, *, clock=None):
        self._crs = graph.graph.get("working_crs")
        if not self._crs or not CRS.from_user_input(self._crs).is_projected:
            raise ValueError('AlertStore requires graph.graph["working_crs"] to be projected')
        self._positions = {node: deepcopy(data.get("pos")) for node, data in graph.nodes(data=True)}
        self._gis_source_ids = {
            node: data.get("gis_source_id") for node, data in graph.nodes(data=True)
        }
        self._alerts = {}
        self._graph_observations = {}
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._lock = RLock()

    @staticmethod
    def _text(value, field):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a nonempty string")

    @staticmethod
    def _validate_resolved(resolved):
        if not isinstance(resolved, ResolvedPriorityAlert):
            raise ValueError("resolved must be a ResolvedPriorityAlert")
        AlertStore._text(resolved.raw_text, "raw_text")
        if type(resolved.is_distress) is not bool:
            raise ValueError("is_distress must be boolean")
        if resolved.node_id is not None and type(resolved.node_id) is not int:
            raise ValueError("node_id must be an integer or None")
        confidence = resolved.confidence
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not math.isfinite(confidence)
            or not 0 <= confidence <= 1
        ):
            raise ValueError("NLP confidence must be finite and within [0, 1]")

    def _now(self):
        now = self._clock()
        self._text(now, "clock timestamp")
        return now

    def receive_alert(
        self,
        alert_id: str,
        resolved: ResolvedPriorityAlert,
        *,
        source: str,
        source_id: str | None = None,
        priority: str | None = None,
        original_event: dict | None = None,
        content_fingerprint: str | None = None,
    ) -> dict:
        """Register immutable source evidence in the explicit INCOMING state."""
        self._text(alert_id, "alert_id")
        self._text(source, "source")
        for value, field in (
            (source_id, "source_id"),
            (priority, "priority"),
            (content_fingerprint, "content_fingerprint"),
        ):
            if value is not None:
                self._text(value, field)
        self._validate_resolved(resolved)
        with self._lock:
            if alert_id in self._alerts:
                raise ValueError(f"Duplicate alert_id: {alert_id}")
            now = self._now()
            record = {
                "alert_id": alert_id,
                "status": INCOMING,
                "report": deepcopy(dict(resolved)),
                "source": source,
                "source_id": source_id,
                "category": "reported_distress" if resolved.is_distress else "reported_incident",
                "priority": priority,
                "created_at": now,
                "original_event": deepcopy(original_event),
                "content_fingerprint": content_fingerprint,
                "responder_confirmation": {
                    "confirmed": False,
                    "responder_id": None,
                    "reviewed_at": None,
                },
                "history": [
                    {
                        "from_status": None,
                        "to_status": INCOMING,
                        "actor_id": None,
                        "at": now,
                    }
                ],
            }
            self._alerts[alert_id] = record
            return deepcopy(record)

    def queue_for_review(self, alert_id: str, *, actor_id: str = "nlp-workflow") -> dict:
        """Complete NLP/association processing and expose the report as pending."""
        self._text(actor_id, "actor_id")
        with self._lock:
            record = self._alerts[alert_id]
            if record["status"] != INCOMING:
                raise InvalidTransition(
                    f"Cannot change {record['status']} to {PENDING_REVIEW}"
                )
            now = self._now()
            record["status"] = PENDING_REVIEW
            record["history"].append(
                {
                    "from_status": INCOMING,
                    "to_status": PENDING_REVIEW,
                    "actor_id": actor_id,
                    "at": now,
                }
            )
            return deepcopy(record)

    def create_alert(self, alert_id: str, resolved: ResolvedPriorityAlert, **kwargs) -> dict:
        """Backward-compatible receive+queue convenience used by existing callers."""
        self.receive_alert(alert_id, resolved, **kwargs)
        return self.queue_for_review(alert_id)

    def _transition(self, alert_id, status, responder_id):
        self._text(responder_id, "responder_id")
        with self._lock:
            record = self._alerts[alert_id]
            if record["status"] != PENDING_REVIEW:
                raise InvalidTransition(f"Cannot change {record['status']} to {status}")
            node_id = record["report"]["node_id"]
            if status == CONFIRMED:
                pos = self._positions.get(node_id)
                if pos is None or len(pos) != 2 or not all(math.isfinite(value) for value in pos):
                    raise ValueError("Approval requires a resolved node with a finite graph position")
            now = self._now()
            record["status"] = status
            record["responder_confirmation"] = {
                "confirmed": status == CONFIRMED,
                "responder_id": responder_id,
                "reviewed_at": now,
            }
            record["history"].append(
                {
                    "from_status": PENDING_REVIEW,
                    "to_status": status,
                    "actor_id": responder_id,
                    "at": now,
                }
            )
            if status == CONFIRMED:
                observation_id = f"social:{alert_id}"
                self._graph_observations[observation_id] = {
                    "observation_id": observation_id,
                    "observation_type": "confirmed_social_report",
                    "alert_id": alert_id,
                    "source": record["source"],
                    "source_id": record["source_id"],
                    "graph_node_id": node_id,
                    "gis_source_id": self._gis_source_ids.get(node_id),
                    "category": record["category"],
                    "confirmed_by": responder_id,
                    "confirmed_at": now,
                    "affects_structural_damage": False,
                    "tgnn_feature": False,
                }
            return deepcopy(record)

    def approve_alert(self, alert_id, *, responder_id):
        return self._transition(alert_id, CONFIRMED, responder_id)

    def report_false(self, alert_id, *, responder_id):
        return self._transition(alert_id, REPORTED_FALSE, responder_id)

    def reject_alert(self, alert_id, *, responder_id):
        return self.report_false(alert_id, responder_id=responder_id)

    def get_alert(self, alert_id):
        with self._lock:
            return deepcopy(self._alerts[alert_id])

    def list_alerts(self, status=None):
        if status not in (None, *ALL_STATUSES):
            raise ValueError("Unknown alert status")
        with self._lock:
            return [
                deepcopy(alert)
                for alert in self._alerts.values()
                if status is None or alert["status"] == status
            ]

    def graph_observations(self):
        """Return confirmed, node-linked evidence; pending/rejected never appear."""
        with self._lock:
            return deepcopy(list(self._graph_observations.values()))

    def confirmed_hotspots(self):
        """Dashboard boundary: confirmed graph locations exported as WGS84."""
        features = []
        for alert in self.list_alerts(CONFIRMED):
            report = alert["report"]
            x, y = self._positions[report["node_id"]]
            lon, lat = working_to_wgs84(x, y, self._crs)
            if not (
                math.isfinite(lon)
                and math.isfinite(lat)
                and -180 <= lon <= 180
                and -90 <= lat <= 90
            ):
                raise ValueError("Graph location cannot be exported as valid WGS84")
            properties = {
                **alert,
                "hotspot_id": f"nlp:{alert['alert_id']}",
                "node_id": report["node_id"],
                "gis_source_id": self._gis_source_ids.get(report["node_id"]),
                "original_text": report["raw_text"],
                "nlp_confidence": report["confidence"],
                "marker_color": "orange",
                "meaning": "A responder confirmed this reported incident.",
                "evidence_layer": "human_confirmed_social_report",
            }
            features.append(
                {
                    "type": "Feature",
                    "id": properties["hotspot_id"],
                    "geometry": {"type": "Point", "coordinates": [lon, lat]},
                    "properties": properties,
                }
            )
        return {"type": "FeatureCollection", "features": features}

    def dashboard_state(self):
        return {
            "incoming_alerts": self.list_alerts(INCOMING),
            "pending_alerts": self.list_alerts(PENDING_REVIEW),
            "confirmed_alerts": self.list_alerts(CONFIRMED),
            "confirmed_hotspots": self.confirmed_hotspots(),
            "reported_false_alerts": self.list_alerts(REPORTED_FALSE),
            "graph_observations": self.graph_observations(),
        }
