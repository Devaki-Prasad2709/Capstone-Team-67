"""Dashboard adapter for Kafka social events and the core alert workflow."""

from __future__ import annotations

from pathlib import Path
from threading import RLock

from core.gis.gis_loader import load_gis
from core.graphs.gis_graph_builder import build_graph_from_gis
from core.nlp.social_alert_workflow import ContradictoryReport, SocialAlertWorkflow


DEFAULT_GIS_PATH = (
    Path(__file__).resolve().parents[1]
    / "scenarios"
    / "louisiana_east_flood"
    / "gis"
    / "infrastructure.geojson"
)


class SocialAlertService:
    """Own one alert workflow and avoid replaying the same Kafka offsets."""

    def __init__(self, gis_path: Path = DEFAULT_GIS_PATH, *, clock=None):
        gis_data = load_gis(str(gis_path))
        graph, id_map = build_graph_from_gis(gis_data, seed=42)
        self.workflow = SocialAlertWorkflow(gis_data, graph, id_map, clock=clock)
        self._seen_messages = set()
        self._ingest_errors = []
        self._lock = RLock()

    def ingest_events(self, events: list[dict]) -> None:
        with self._lock:
            for event in reversed(events):
                token = (event.get("_kafka_partition"), event.get("_kafka_offset"))
                # Unit/direct callers may not have Kafka metadata, so use a token
                # only when both coordinates are available.
                has_token = all(value is not None for value in token)
                if has_token and token in self._seen_messages:
                    continue
                try:
                    self.workflow.ingest_event(event)
                except (ValueError, ContradictoryReport) as exc:
                    self._ingest_errors.append(
                        {
                            "id": event.get("id"),
                            "kafka_partition": token[0],
                            "kafka_offset": token[1],
                            "error": str(exc),
                        }
                    )
                finally:
                    if has_token:
                        self._seen_messages.add(token)

    def decide(self, alert_id: str, action: str, responder_id: str) -> dict:
        with self._lock:
            return self.workflow.decide(alert_id, action, responder_id=responder_id)

    def state(self) -> dict:
        with self._lock:
            state = self.workflow.dashboard_state()
            state["ingest_errors"] = list(self._ingest_errors)
            return state
