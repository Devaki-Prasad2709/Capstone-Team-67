"""Social event -> NLP/GIS resolution -> responder-review workflow."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import re

from core.gis.gis_loader import wgs84_to_working
from core.nlp.alert_state import AlertStore
from core.nlp.landmark_alert_resolver import resolve_alert


class ContradictoryReport(ValueError):
    """The same source identity was reused for materially different evidence."""


@dataclass(frozen=True)
class IngestOutcome:
    outcome: str
    alert_id: str
    canonical_alert_id: str
    status: str


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _fingerprint(event: dict) -> str:
    evidence = {
        "text": _normalized_text(event["text"]),
        "source": event.get("source"),
        "event": event.get("event"),
        "gps": event.get("gps"),
        "target_ids": event.get("target_ids"),
    }
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


class SocialAlertWorkflow:
    """Coordinates source deduplication, NLP resolution and review decisions."""

    def __init__(self, gis_data, graph, id_map, *, clock=None):
        self.gis_data = gis_data
        self.graph = graph
        self.id_map = dict(id_map)
        self.store = AlertStore(graph, clock=clock)
        self._by_source_id = {}
        self._by_fingerprint = {}
        self._outcomes = []

    @staticmethod
    def _validate_event(event):
        if not isinstance(event, dict):
            raise ValueError("Social event must be an object")
        for field in ("id", "text", "source"):
            if not isinstance(event.get(field), str) or not event[field].strip():
                raise ValueError(f"Social event {field} must be a nonempty string")
        gps = event.get("gps")
        if gps is not None:
            if not isinstance(gps, dict):
                raise ValueError("gps must be an object")
            lon, lat = gps.get("longitude"), gps.get("latitude")
            if (
                isinstance(lon, bool)
                or isinstance(lat, bool)
                or not isinstance(lon, (int, float))
                or not isinstance(lat, (int, float))
                or not math.isfinite(lon)
                or not math.isfinite(lat)
                or not -180 <= lon <= 180
                or not -90 <= lat <= 90
            ):
                raise ValueError("gps must contain finite WGS84 longitude/latitude")

    def ingest_event(self, event: dict) -> IngestOutcome:
        """Ingest once, recording INCOMING then PENDING_REVIEW transitions."""
        self._validate_event(event)
        alert_id = event["id"].strip()
        source_key = (event["source"].strip(), alert_id)
        fingerprint = _fingerprint(event)

        if source_key in self._by_source_id:
            canonical = self._by_source_id[source_key]
            existing = self.store.get_alert(canonical)
            if existing["content_fingerprint"] != fingerprint:
                raise ContradictoryReport(
                    f"Source report {source_key!r} conflicts with accepted alert {canonical}"
                )
            outcome = IngestOutcome("duplicate", alert_id, canonical, existing["status"])
            self._outcomes.append(deepcopy(outcome.__dict__))
            return outcome

        if fingerprint in self._by_fingerprint:
            canonical = self._by_fingerprint[fingerprint]
            existing = self.store.get_alert(canonical)
            outcome = IngestOutcome("duplicate", alert_id, canonical, existing["status"])
            self._by_source_id[source_key] = canonical
            self._outcomes.append(deepcopy(outcome.__dict__))
            return outcome

        reference_pos = None
        if event.get("gps"):
            reference_pos = wgs84_to_working(
                event["gps"]["longitude"],
                event["gps"]["latitude"],
                self.gis_data.working_crs,
            )
        resolved = resolve_alert(event["text"], self.gis_data, self.id_map, reference_pos)
        self.store.receive_alert(
            alert_id,
            resolved,
            source=event["source"],
            source_id=alert_id,
            original_event=event,
            content_fingerprint=fingerprint,
        )
        pending = self.store.queue_for_review(alert_id)
        self._by_source_id[source_key] = alert_id
        self._by_fingerprint[fingerprint] = alert_id
        outcome = IngestOutcome("accepted", alert_id, alert_id, pending["status"])
        self._outcomes.append(deepcopy(outcome.__dict__))
        return outcome

    def decide(self, alert_id: str, action: str, *, responder_id: str) -> dict:
        if action == "confirm":
            return self.store.approve_alert(alert_id, responder_id=responder_id)
        if action in {"reject", "report_false"}:
            return self.store.report_false(alert_id, responder_id=responder_id)
        raise ValueError("action must be confirm, reject, or report_false")

    def dashboard_state(self):
        state = self.store.dashboard_state()
        state["ingest_outcomes"] = deepcopy(self._outcomes)
        state["counts"] = {
            "incoming": len(state["incoming_alerts"]),
            "pending": len(state["pending_alerts"]),
            "confirmed": len(state["confirmed_alerts"]),
            "rejected": len(state["reported_false_alerts"]),
            "graph_observations": len(state["graph_observations"]),
        }
        return state
