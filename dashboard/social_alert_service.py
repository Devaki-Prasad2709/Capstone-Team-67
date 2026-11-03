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
        self.gis_path = Path(gis_path)
        self.clock = clock
        gis_data = load_gis(str(self.gis_path))
        graph, id_map = build_graph_from_gis(gis_data, seed=42)
        self.workflow = SocialAlertWorkflow(gis_data, graph, id_map, clock=clock)
        self._seen_messages = set()
        self._ingest_errors = []
        self._lock = RLock()

    def reset(self) -> None:
        """Clear replay-local review state while leaving Kafka history immutable."""
        with self._lock:
            gis_data = load_gis(str(self.gis_path))
            graph, id_map = build_graph_from_gis(gis_data, seed=42)
            self.workflow = SocialAlertWorkflow(
                gis_data, graph, id_map, clock=self.clock
            )
            self._seen_messages.clear()
            self._ingest_errors.clear()

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

    def rebuild(self, events: list[dict], reviews: list[dict]) -> None:
        """Deterministically restore alerts and persisted responder decisions."""
        self.reset()
        # ingest_events accepts Kafka-tail order (newest first), while the
        # durable materialization is stored oldest first.
        newest_first = sorted(
            events,
            key=lambda item: (
                str(item.get("scenario_timestamp", item.get("timestamp", ""))),
                int(item.get("_kafka_partition", 0)),
                int(item.get("_kafka_offset", 0)),
            ),
            reverse=True,
        )
        self.ingest_events(newest_first)
        for review in reviews:
            try:
                self.decide(
                    review["alert_id"],
                    review["action"],
                    review["responder_id"],
                )
            except (KeyError, ValueError):
                # A review whose source event is unavailable remains durable in
                # SQLite and can be applied when that event is materialized.
                continue

    def decide(self, alert_id: str, action: str, responder_id: str) -> dict:
        with self._lock:
            return self.workflow.decide(alert_id, action, responder_id=responder_id)

    def has_alert(self, alert_id: str) -> bool:
        """Return whether this isolated workflow owns an alert identity."""
        with self._lock:
            try:
                self.workflow.store.get_alert(alert_id)
            except KeyError:
                return False
            return True

    def state(self) -> dict:
        with self._lock:
            state = self.workflow.dashboard_state()
            state["ingest_errors"] = list(self._ingest_errors)
            node_to_gis = {}
            for gis_source_id, graph_node_id in self.workflow.id_map.items():
                node_to_gis.setdefault(graph_node_id, gis_source_id)
            alerts = (
                state["incoming_alerts"]
                + state["pending_alerts"]
                + state["confirmed_alerts"]
                + state["reported_false_alerts"]
            )
            state["associations"] = [
                {
                    "alert_id": alert["alert_id"],
                    "gis_source_id": node_to_gis.get(alert["report"].get("node_id")),
                    "graph_node_id": alert["report"].get("node_id"),
                }
                for alert in alerts
            ]
            return state
