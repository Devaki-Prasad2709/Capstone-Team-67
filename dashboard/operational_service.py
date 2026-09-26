"""Build the incident-first dashboard projection from existing core contracts."""

from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json
from pathlib import Path
from threading import RLock

from consumers.observation_consumer import initialize_state, process_message
from core.gis.building_classification import apply_building_classifications
from core.integration.tgnn_predictor import (
    CALIBRATION_WARNING,
    predict_node_risk_report,
)
from core.observation.state_update import apply_observations
from core.observation.telemetry import apply_telemetry_event
from core.overlay.gis_overlay import build_node_overlay


ROOT = Path(__file__).resolve().parents[1]
SCENARIO_DIR = ROOT / "scenarios" / "louisiana_east_flood"
GIS_PATH = SCENARIO_DIR / "gis" / "infrastructure.geojson"
SCENARIO_PATH = SCENARIO_DIR / "scenario.json"


def _event_time(event: dict) -> float:
    for field in ("source_timestamp", "timestamp", "processed_at"):
        value = event.get(field)
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return 0.0


def _iso(epoch: float | None) -> str | None:
    if epoch is None:
        return None
    return datetime.fromtimestamp(float(epoch), timezone.utc).isoformat().replace("+00:00", "Z")


class OperationalDashboardService:
    """Rebuild a deterministic graph view from bounded Kafka topic tails."""

    def __init__(self, building_service, *, gis_path: Path = GIS_PATH):
        self.building_service = building_service
        self.gis_path = Path(gis_path)
        self.scenario = json.loads(SCENARIO_PATH.read_text(encoding="utf-8"))
        self.timeline = json.loads(
            (SCENARIO_DIR / "timeline.json").read_text(encoding="utf-8")
        )
        self.structural = json.loads(self.gis_path.read_text(encoding="utf-8"))
        self._lock = RLock()
        self._cache_key = None
        self._cache = None

    @staticmethod
    def _cache_token(events: list[dict]) -> tuple:
        return tuple(
            sorted(
                (
                    str(item.get("_kafka_partition", "")),
                    str(item.get("_kafka_offset", "")),
                    str(item.get("id") or item.get("frame_id") or ""),
                    _event_time(item),
                )
                for item in events
            )
        )

    def _structural_layer(self) -> dict:
        payload = copy.deepcopy(self.structural)
        for feature in payload.get("features", []):
            properties = feature.setdefault("properties", {})
            properties["provenance"] = "real-openstreetmap-snapshot"
            properties["provenance_label"] = "REAL OSM"
            if properties.get("feature_class") == "building":
                source_id = str(properties.get("building_id", ""))
                if source_id and self.building_service.store.recognizes(source_id):
                    classification = self.building_service.store.get(source_id)
                    properties["classification"] = classification
                    properties["assigned_type"] = (
                        classification["assigned_type"] if classification else None
                    )
                    properties["graph_node_id"] = self.building_service.id_map.get(source_id)
        return payload

    def snapshot(self, ai_events: list[dict], telemetry_events: list[dict]) -> dict:
        classification_token = tuple(
            (item["building_source_id"], item["revision_id"])
            for item in self.building_service.store.list_current()
        )
        key = (
            self._cache_token(ai_events),
            self._cache_token(telemetry_events),
            classification_token,
        )
        with self._lock:
            if key == self._cache_key and self._cache is not None:
                # Building classification can change without a Kafka offset change.
                result = copy.deepcopy(self._cache)
                result["layers"]["structural_gis"] = self._structural_layer()
                return result

            state = initialize_state(
                str(self.gis_path), allow_scenario_simulated_gps=True
            )
            state.spatial_index.classification_store = self.building_service.store
            baseline = copy.deepcopy(state.graph)
            snapshots = [baseline]
            errors = []
            accepted = []

            for event in sorted(ai_events, key=_event_time):
                if event.get("scenario_id") != self.scenario["scenario_id"]:
                    continue
                try:
                    outcome = process_message(
                        json.dumps(event, separators=(",", ":")).encode("utf-8"), state
                    )
                    accepted.extend(outcome.accepted_observation_ids)
                except Exception as exc:
                    errors.append({
                        "source": "ai-analysis-results",
                        "event_id": event.get("frame_id"),
                        "message": str(exc),
                    })

            records = [state.observation_log.get(item) for item in accepted]
            records = [item for item in records if item is not None]
            if records:
                graph = apply_observations(
                    baseline,
                    state.observation_log,
                    now=max(item.timestamp for item in records),
                )
                snapshots.append(graph)
            else:
                graph = baseline

            applied_telemetry = []
            seen_telemetry_ids: set[str] = set()
            for event in sorted(telemetry_events, key=_event_time):
                if event.get("scenario_id") != self.scenario["scenario_id"]:
                    continue
                event_id = event.get("id")
                if isinstance(event_id, str) and event_id in seen_telemetry_ids:
                    continue
                try:
                    graph = apply_telemetry_event(graph, self.building_service.id_map, event)
                    snapshots.append(graph)
                    applied_telemetry.append(copy.deepcopy(event))
                    if isinstance(event_id, str):
                        seen_telemetry_ids.add(event_id)
                except Exception as exc:
                    errors.append({
                        "source": "infrastructure-telemetry",
                        "event_id": event.get("id"),
                        "message": str(exc),
                    })

            risk_report = predict_node_risk_report(snapshots)
            risk_nodes = risk_report["timeline"][-1]["nodes"]
            risk_by_id = {
                item["graph_node_id"]: item["failure_risk_score"]
                for item in risk_nodes
            }
            enriched = apply_building_classifications(graph, self.building_service.store)
            node_overlay = build_node_overlay(enriched, risk_by_id)
            by_node = {int(item["graph_node_id"]): item for item in risk_nodes}
            ranked = sorted(risk_nodes, key=lambda item: item["risk_rank"])

            for feature in node_overlay["features"]:
                node_id = int(feature["properties"]["id"])
                node = enriched.nodes[node_id]
                risk = by_node[node_id]
                properties = feature["properties"]
                properties.update({
                    "gis_source_id": node.get("gis_source_id"),
                    "status": node.get("status_label"),
                    "effective_capacity": round(float(node.get("effective_capacity", 0)), 4),
                    "utilization": round(float(node.get("utilization", 0)), 4),
                    "freshness_status": node.get("freshness_status"),
                    "risk_rank": risk["risk_rank"],
                    "highest_risk": risk["risk_rank"] == 1,
                    "classification": node.get("building_classification"),
                    "assigned_building_type": node.get("assigned_building_type"),
                    "provenance": "model-derived-from-real-gis-and-stream-evidence",
                    "provenance_label": "MODEL-DERIVED",
                })

            damage_features = []
            for record in records:
                damage_features.append({
                    "type": "Feature",
                    "geometry": {
                        "type": "Point",
                        "coordinates": [record.longitude, record.latitude],
                    },
                    "properties": {
                        **asdict(record),
                        "bbox": list(record.bbox),
                        "image_provenance": "real-isbda",
                        "location_provenance": record.location_provenance,
                        "provenance_label": "REAL IMAGE / SIMULATED GPS",
                    },
                })

            def risk_summary(item: dict) -> dict:
                node = enriched.nodes[item["graph_node_id"]]
                return {
                    **item,
                    "status": node.get("status_label"),
                    "damage": round(float(node.get("damage", 0)), 4),
                    "stress": round(float(node.get("stress", 0)), 4),
                    "freshness_status": node.get("freshness_status"),
                    "last_updated": _iso(node.get("state_timestamp")),
                    "why": (
                        f"damage {float(node.get('damage', 0)):.2f}, "
                        f"stress {float(node.get('stress', 0)):.2f}, "
                        f"status {node.get('status_label', 'unknown')}"
                    ),
                }

            top_five = [risk_summary(item) for item in ranked[:5]]
            graph_timestamp = graph.graph.get("snapshot_timestamp")
            result = {
                "scenario": {
                    "id": self.scenario["scenario_id"],
                    "title": self.scenario["title"],
                    "location": self.scenario["location"],
                },
                "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "layers": {
                    "structural_gis": self._structural_layer(),
                    "damage_observations": {
                        "type": "FeatureCollection", "features": damage_features
                    },
                    "tgnn_risk": node_overlay,
                },
                "top_five_risk_nodes": top_five,
                "highest_risk_node": top_five[0] if top_five else None,
                "telemetry_events": applied_telemetry,
                "freshness": {
                    "graph": _iso(graph_timestamp) or self.timeline["scenario_start"],
                    "risk": _iso(graph_timestamp) or self.timeline["scenario_start"],
                    "latest_damage_observation": _iso(max((r.timestamp for r in records), default=None)),
                    "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                },
                "provenance": {
                    "structural_gis": {"kind": "real", "label": "Real OSM snapshot"},
                    "drone_imagery": {"kind": "mixed", "label": "Real ISBDA image; simulated Louisiana GPS/time"},
                    "satellite": {"kind": "mixed", "label": "Real SpaceNet pre/post imagery; simulated scenario time"},
                    "social": {"kind": "simulated", "label": "Simulated report text, position, targets, and time"},
                    "telemetry": {"kind": "simulated", "label": "Simulated scenario telemetry"},
                    "risk": {"kind": "derived", "label": "Uncalibrated TGNN relative score"},
                },
                "risk_interpretation": risk_report["risk_interpretation"],
                "calibrated_probability": False,
                "calibration_warning": CALIBRATION_WARNING,
                "counts": {
                    "structural_features": len(self.structural.get("features", [])),
                    "damage_observations": len(damage_features),
                    "risk_nodes": len(node_overlay["features"]),
                },
                "errors": errors,
            }
            self._cache_key = key
            self._cache = copy.deepcopy(result)
            return result
