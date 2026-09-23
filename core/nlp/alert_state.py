"""Dashboard-only responder review state; never writes to a graph or TGNN.

One store belongs to one graph snapshot. In-memory only: callers must provide
persistence and authenticated responder identities at their service boundary.
"""

from copy import deepcopy
from datetime import datetime, timezone
import math
from threading import RLock

from pyproj import CRS

from core.gis.gis_loader import working_to_wgs84
from core.nlp.landmark_alert_resolver import ResolvedPriorityAlert

PENDING_REVIEW = "PENDING_REVIEW"
CONFIRMED = "CONFIRMED"
REPORTED_FALSE = "REPORTED_FALSE"


class InvalidTransition(ValueError):
    """A terminal review decision cannot be repeated or silently replaced."""


class AlertStore:
    def __init__(self, graph, *, clock=None):
        self._crs = graph.graph.get("working_crs")
        if not self._crs or not CRS.from_user_input(self._crs).is_projected:
            raise ValueError('AlertStore requires graph.graph["working_crs"] to be projected')
        self._positions = {n: deepcopy(d.get("pos")) for n, d in graph.nodes(data=True)}
        self._alerts = {}
        self._clock = clock or (lambda: datetime.now(timezone.utc).isoformat())
        self._lock = RLock()

    @staticmethod
    def _text(value, field):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be a nonempty string")

    def create_alert(self, alert_id: str, resolved: ResolvedPriorityAlert, *,
                     source: str, source_id: str | None = None,
                     priority: str | None = None) -> dict:
        """Preserve resolver evidence; priority is supplied, never inferred here."""
        self._text(alert_id, "alert_id")
        self._text(source, "source")
        for value, field in [(source_id, "source_id"), (priority, "priority")]:
            if value is not None:
                self._text(value, field)
        self._text(resolved.raw_text, "raw_text")
        if type(resolved.is_distress) is not bool:
            raise ValueError("is_distress must be boolean")
        if resolved.node_id is not None and type(resolved.node_id) is not int:
            raise ValueError("node_id must be an integer or None")
        if (isinstance(resolved.confidence, bool) or not isinstance(resolved.confidence, (int, float))
                or not math.isfinite(resolved.confidence) or not 0 <= resolved.confidence <= 1):
            raise ValueError("NLP confidence must be finite and within [0, 1]")
        with self._lock:
            if alert_id in self._alerts:
                raise ValueError(f"Duplicate alert_id: {alert_id}")
            now = self._clock()
            self._text(now, "clock timestamp")
            record = {
                "alert_id": alert_id, "status": PENDING_REVIEW,
                "report": deepcopy(dict(resolved)), "source": source, "source_id": source_id,
                "category": "reported_distress" if resolved.is_distress else "reported_incident",
                "priority": priority, "created_at": now,
                "responder_confirmation": {"confirmed": False, "responder_id": None, "reviewed_at": None},
                "history": [{"from_status": None, "to_status": PENDING_REVIEW,
                             "responder_id": None, "at": now}],
            }
            self._alerts[alert_id] = record
            return deepcopy(record)

    def _transition(self, alert_id, status, responder_id):
        self._text(responder_id, "responder_id")
        with self._lock:
            record = self._alerts[alert_id]  # Unknown IDs explicitly raise KeyError.
            if record["status"] != PENDING_REVIEW:
                raise InvalidTransition(f"Cannot change {record['status']} to {status}")
            if status == CONFIRMED:
                pos = self._positions.get(record["report"]["node_id"])
                if pos is None or len(pos) != 2 or not all(math.isfinite(v) for v in pos):
                    raise ValueError("Approval requires a resolved node with a finite graph position")
            now = self._clock()
            self._text(now, "clock timestamp")
            record["status"] = status
            record["responder_confirmation"] = {
                "confirmed": status == CONFIRMED, "responder_id": responder_id, "reviewed_at": now,
            }
            record["history"].append({"from_status": PENDING_REVIEW, "to_status": status,
                                      "responder_id": responder_id, "at": now})
            return deepcopy(record)

    def approve_alert(self, alert_id, *, responder_id):
        return self._transition(alert_id, CONFIRMED, responder_id)

    def report_false(self, alert_id, *, responder_id):
        return self._transition(alert_id, REPORTED_FALSE, responder_id)

    def get_alert(self, alert_id):
        with self._lock:
            return deepcopy(self._alerts[alert_id])

    def list_alerts(self, status=None):
        if status not in (None, PENDING_REVIEW, CONFIRMED, REPORTED_FALSE):
            raise ValueError("Unknown alert status")
        with self._lock:
            return [deepcopy(a) for a in self._alerts.values() if status is None or a["status"] == status]

    def confirmed_hotspots(self):
        """Dashboard/export boundary: graph working CRS -> WGS84 GeoJSON."""
        features = []
        for alert in self.list_alerts(CONFIRMED):
            report = alert["report"]
            x, y = self._positions[report["node_id"]]
            lon, lat = working_to_wgs84(x, y, self._crs)
            if not (math.isfinite(lon) and math.isfinite(lat) and -180 <= lon <= 180 and -90 <= lat <= 90):
                raise ValueError("Graph location cannot be exported as valid WGS84")
            properties = {**alert, "hotspot_id": f"nlp:{alert['alert_id']}",
                          "node_id": report["node_id"], "original_text": report["raw_text"],
                          "nlp_confidence": report["confidence"],
                          "meaning": "First responder confirmed this reported incident.",
                          "evidence_layer": "human_confirmed_social_report"}
            features.append({"type": "Feature", "id": properties["hotspot_id"],
                             "geometry": {"type": "Point", "coordinates": [lon, lat]},
                             "properties": properties})
        return {"type": "FeatureCollection", "features": features}

    def dashboard_state(self):
        with self._lock:
            return {"pending_alerts": self.list_alerts(PENDING_REVIEW),
                    "confirmed_hotspots": self.confirmed_hotspots(),
                    "reported_false_alerts": self.list_alerts(REPORTED_FALSE)}
