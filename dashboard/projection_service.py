"""Compose one internally consistent, cached Operations-page snapshot."""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone
from threading import RLock

from dashboard.cache_service import utc_now


def _event_epoch(value) -> float | None:
    try:
        if isinstance(value, str):
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        return float(value)
    except (TypeError, ValueError):
        return None


def visible_at(rows: list[dict], scenario: dict) -> list[dict]:
    if scenario.get("state") == "idle":
        return []
    cutoff = _event_epoch(scenario.get("simulation_timestamp"))
    if cutoff is None:
        return []
    visible = []
    for row in rows:
        timestamp = row.get("scenario_timestamp", row.get("timestamp"))
        epoch = _event_epoch(timestamp)
        if epoch is not None and epoch <= cutoff:
            visible.append(row)
    return visible


def _latest_evidence_time(rows: list[dict]) -> str | float | None:
    candidates = [
        row.get("scenario_timestamp", row.get("timestamp")) for row in rows
    ]
    candidates = [item for item in candidates if _event_epoch(item) is not None]
    return max(candidates, key=_event_epoch) if candidates else None


class DashboardProjectionService:
    """Build the entire Operations payload from one scenario-time boundary."""

    def __init__(
        self,
        *,
        operational_service,
        scenario_service,
        social_service,
        building_service,
        materialized_state,
        topics,
        map_config_loader,
        object_key_resolver=None,
        ttl_seconds: float = 6.0,
        clock=None,
    ):
        self.operational_service = operational_service
        self.scenario_service = scenario_service
        self.social_service = social_service
        self.building_service = building_service
        self.materialized_state = materialized_state
        self.topics = topics
        self.map_config_loader = map_config_loader
        self.object_key_resolver = object_key_resolver
        self.ttl_seconds = ttl_seconds
        self.clock = clock or time.monotonic
        self._cache: dict | None = None
        self._cache_token = None
        self._cached_at = 0.0
        self._lock = RLock()

    def invalidate(self) -> None:
        with self._lock:
            self._cache = None
            self._cache_token = None

    def cached_freshness(self) -> dict:
        """Return timestamps already produced by Operations without triggering work."""
        with self._lock:
            return copy.deepcopy((self._cache or {}).get("evidence_freshness", {}))

    def snapshot(self) -> dict:
        request_started = self.clock()
        with self._lock:
            scenario = self.scenario_service.status()
            token = (
                scenario.get("state"),
                scenario.get("simulation_timestamp"),
                tuple(
                    item.get("event_id") if isinstance(item, dict) else str(item)
                    for item in (scenario.get("completed_events") or [])
                ),
            )
            now = self.clock()
            if (
                self._cache is not None
                and token == self._cache_token
                and now - self._cached_at < self.ttl_seconds
            ):
                result = copy.deepcopy(self._cache)
                result["diagnostics"].update({
                    "snapshot_cache_hit": True,
                    "cache_age_ms": round((now - self._cached_at) * 1000, 3),
                    "request_duration_ms": round((self.clock() - request_started) * 1000, 3),
                })
                return result

            materialization = self.materialized_state.refresh()
            scenario_id = self.operational_service.scenario["scenario_id"]
            ai_rows = visible_at(
                self.materialized_state.events(
                    self.topics["ai"], scenario_id=scenario_id, replay_only=True
                ),
                scenario,
            )
            telemetry_rows = visible_at(
                self.materialized_state.events(
                    self.topics["telemetry"],
                    scenario_id=scenario_id,
                    replay_only=True,
                ),
                scenario,
            )
            social_rows = visible_at(
                self.materialized_state.events(
                    self.topics["social"], scenario_id=scenario_id, replay_only=True
                ),
                scenario,
            )
            satellite_rows = visible_at(
                self.materialized_state.events(
                    self.topics["satellite"],
                    scenario_id=scenario_id,
                    replay_only=True,
                ),
                scenario,
            )
            completed_events = [
                item
                for item in (scenario.get("completed_events") or [])
                if isinstance(item, dict)
            ]
            # Scenario packages are free to use descriptive event IDs.  The
            # stable contract for a TGNN milestone is its risk_set target, not
            # a Louisiana-specific event name.
            risk_due = any(
                (item.get("target") or {}).get("kind") == "risk_set"
                for item in completed_events
            )
            operational = self.operational_service.snapshot(
                ai_rows, telemetry_rows, include_risk=risk_due
            )
            reviews = self.materialized_state.reviews(
                scope="scenario", scenario_id=scenario_id
            )
            self.social_service.rebuild(social_rows, reviews)
            social = self.social_service.state()
            social.update(scope="scenario", scenario_id=scenario_id)

            latest_satellite = (
                copy.deepcopy(
                    max(
                        satellite_rows,
                        key=lambda row: _event_epoch(
                            row.get("scenario_timestamp", row.get("timestamp"))
                        ) or 0.0,
                    )
                )
                if satellite_rows
                else None
            )
            if latest_satellite:
                self._add_satellite_previews(latest_satellite)
            satellite = {
                "latest": latest_satellite,
                "result_count": len(satellite_rows),
                "error": materialization["sources"][self.topics["satellite"]]["error"],
            }

            building_overlay = self.building_service.list_buildings()
            buildings = {
                "buildings": building_overlay,
                "count": len(building_overlay["features"]),
                "classified_count": sum(
                    feature["properties"]["classification"] is not None
                    for feature in building_overlay["features"]
                ),
                "raw_gis_mutated": False,
            }
            generated_at = utc_now()
            source_errors = {
                "ai_analysis_results": materialization["sources"][self.topics["ai"]]["error"],
                "infrastructure_telemetry": materialization["sources"][self.topics["telemetry"]]["error"],
                "social_posts": materialization["sources"][self.topics["social"]]["error"],
                "satellite_change_results": materialization["sources"][self.topics["satellite"]]["error"],
            }
            observation_ids = [
                str(feature.get("properties", {}).get("observation_id"))
                for feature in operational.get("layers", {})
                .get("damage_observations", {})
                .get("features", [])
                if feature.get("properties", {}).get("observation_id") is not None
            ]
            telemetry_ids = [
                str(item["id"])
                for item in operational.get("telemetry_events", [])
                if item.get("id") is not None
            ]
            self.materialized_state.record_processed_ids(
                scenario_id, observation_ids, telemetry_ids
            )
            result = {
                "operational": operational,
                "scenario": scenario,
                "social": social,
                "satellite": satellite,
                "buildings": buildings,
                "map_config": self.map_config_loader(),
                "evidence_freshness": {
                    **operational.get("freshness", {}),
                    "scenario": scenario.get("simulation_timestamp"),
                    "social": _latest_evidence_time(social_rows),
                    "satellite": _latest_evidence_time(satellite_rows),
                    "snapshot": generated_at,
                },
                "source_errors": source_errors,
                "diagnostics": {
                    "snapshot_cache_hit": False,
                    "cached_at": generated_at,
                    "cache_age_ms": 0.0,
                    "request_duration_ms": round((self.clock() - request_started) * 1000, 3),
                    "tgnn_cache_hit": operational.get("diagnostics", {}).get(
                        "tgnn_cache_hit", False
                    ),
                    "materialization": materialization,
                    "processed_evidence": self.materialized_state.processed_ids(
                        scenario_id
                    ),
                },
            }
            self._cache = copy.deepcopy(result)
            self._cache_token = token
            self._cached_at = self.clock()
            return result

    def _add_satellite_previews(self, result: dict) -> None:
        for image in (result.get("source_images") or {}).values():
            if not isinstance(image, dict):
                continue
            key = image.get("object_key")
            if not key and self.object_key_resolver:
                key = self.object_key_resolver("satellite", image.get("content_hash"))
            if isinstance(key, str) and key.startswith("satellite/"):
                image["preview_url"] = "/api/object?key=" + key
