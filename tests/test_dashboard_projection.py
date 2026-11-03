from __future__ import annotations

from dashboard.cache_service import HealthCache, TopicTailCache
from dashboard.projection_service import DashboardProjectionService


class OperationalStub:
    scenario = {"scenario_id": "scenario-1"}

    def __init__(self):
        self.calls = 0

    def snapshot(self, ai_rows, telemetry_rows, *, include_risk=True):
        self.calls += 1
        self.ai_count = len(ai_rows)
        self.telemetry_count = len(telemetry_rows)
        self.include_risk = include_risk
        return {
            "layers": {},
            "freshness": {"graph": "2026-01-01T00:00:00Z", "risk": "2026-01-01T00:00:00Z"},
            "errors": [],
            "diagnostics": {"tgnn_cache_hit": self.calls > 1},
        }


class ScenarioStub:
    def status(self):
        return {
            "state": "paused",
            "simulation_timestamp": "2026-01-01T00:10:00Z",
            "completed_events": [],
        }


class ScenarioWithGenericRiskMilestoneStub:
    def status(self):
        return {
            "state": "completed",
            "simulation_timestamp": "2026-01-01T00:10:00Z",
            "completed_events": [
                {
                    "event_id": "scenario-specific-tgnn-name",
                    "target": {"kind": "risk_set", "ids": ["all"]},
                }
            ],
        }


class SocialStub:
    def __init__(self):
        self.rows = []

    def rebuild(self, rows, reviews):
        self.rows = list(rows)

    def state(self):
        return {
            "counts": {"pending": len(self.rows)},
            "confirmed_hotspots": {"type": "FeatureCollection", "features": []},
        }


class BuildingStub:
    def list_buildings(self):
        return {
            "type": "FeatureCollection",
            "features": [{"properties": {"classification": None}}],
        }


class MaterializedStub:
    def __init__(self, rows):
        self.rows = rows
        self.refresh_calls = 0
        self.recorded = None

    def refresh(self):
        self.refresh_calls += 1
        return {
            "sources": {
                topic: {"error": None, "read": len(rows), "inserted": len(rows)}
                for topic, rows in self.rows.items()
            },
            "cache_hit": False,
        }

    def events(self, topic, **kwargs):
        return list(self.rows[topic])

    def reviews(self, **kwargs):
        return []

    def record_processed_ids(self, scenario_id, observation_ids, telemetry_ids):
        self.recorded = (scenario_id, observation_ids, telemetry_ids)

    def processed_ids(self, scenario_id):
        return {"observation": [], "telemetry": []}


class PreviousReplaySatelliteStub(MaterializedStub):
    def events(self, topic, **kwargs):
        if topic == "satellite-change-results" and kwargs.get("replay_only"):
            return []
        return super().events(topic, **kwargs)


def test_projection_is_consistent_and_reuses_cached_materialization():
    common = {
        "scenario_id": "scenario-1",
        "scenario_timestamp": "2026-01-01T00:05:00Z",
    }
    rows = {
        "ai-analysis-results": [{**common, "frame_id": f"frame-{index}"} for index in range(250)],
        "infrastructure-telemetry": [{**common, "id": f"telemetry-{index}"} for index in range(150)],
        "social-posts": [{**common, "id": "social-1"}],
        "satellite-change-results": [{**common, "id": "satellite-1"}],
    }
    materialized = MaterializedStub(rows)
    operational = OperationalStub()
    projection = DashboardProjectionService(
        operational_service=operational,
        scenario_service=ScenarioStub(),
        social_service=SocialStub(),
        building_service=BuildingStub(),
        materialized_state=materialized,
        topics={
            "ai": "ai-analysis-results",
            "telemetry": "infrastructure-telemetry",
            "social": "social-posts",
            "satellite": "satellite-change-results",
        },
        map_config_loader=lambda: {"scenario": {"id": "scenario-1"}},
        ttl_seconds=60,
    )

    first = projection.snapshot()
    second = projection.snapshot()

    assert materialized.refresh_calls == 1
    assert operational.calls == 1
    assert operational.ai_count == 250
    assert operational.telemetry_count == 150
    assert operational.include_risk is False
    assert first["scenario"]["simulation_timestamp"] == first["evidence_freshness"]["scenario"]
    assert first["social"]["counts"]["pending"] == 1
    assert first["satellite"]["latest"]["id"] == "satellite-1"
    assert first["buildings"]["count"] == 1
    assert first["diagnostics"]["snapshot_cache_hit"] is False
    assert second["diagnostics"]["snapshot_cache_hit"] is True
    assert "request_duration_ms" in second["diagnostics"]


def test_topic_and_health_caches_report_hits_and_timestamps():
    topic_calls = []
    tails = TopicTailCache(
        lambda topic, limit: (topic_calls.append(topic) or ([{"id": "1"}], None)),
        ttl_seconds=60,
    )
    _, _, first_tail = tails.get("social-posts", 10)
    _, _, second_tail = tails.get("social-posts", 10)
    assert topic_calls == ["social-posts"]
    assert first_tail["cache_hit"] is False
    assert second_tail["cache_hit"] is True
    assert second_tail["cached_at"].endswith("Z")

    health_calls = []
    health = HealthCache(ttl_seconds=60)
    loader = lambda: (health_calls.append(True) or ({"ok": True}, None))
    _, _, first_health = health.get("docker", loader)
    _, _, second_health = health.get("docker", loader)
    assert len(health_calls) == 1
    assert first_health["cache_hit"] is False
    assert second_health["cache_hit"] is True
    assert "request_duration_ms" in second_health


def test_projection_does_not_leak_previous_satellite_result_into_new_replay():
    timestamp = "2026-01-01T00:05:00Z"
    rows = {
        "ai-analysis-results": [],
        "infrastructure-telemetry": [],
        "social-posts": [],
        "satellite-change-results": [
            {
                "scenario_id": "scenario-1",
                "scenario_timestamp": timestamp,
                "id": "previous-satellite-result",
            }
        ],
    }
    projection = DashboardProjectionService(
        operational_service=OperationalStub(),
        scenario_service=ScenarioStub(),
        social_service=SocialStub(),
        building_service=BuildingStub(),
        materialized_state=PreviousReplaySatelliteStub(rows),
        topics={
            "ai": "ai-analysis-results",
            "telemetry": "infrastructure-telemetry",
            "social": "social-posts",
            "satellite": "satellite-change-results",
        },
        map_config_loader=lambda: {"scenario": {"id": "scenario-1"}},
    )

    snapshot = projection.snapshot()

    assert snapshot["satellite"]["latest"] is None
    assert snapshot["satellite"]["result_count"] == 0
    assert "stale_previous_replay" not in snapshot["satellite"]


def test_projection_uses_risk_target_contract_instead_of_scenario_specific_event_id():
    common = {
        "scenario_id": "scenario-1",
        "scenario_timestamp": "2026-01-01T00:05:00Z",
    }
    rows = {
        "ai-analysis-results": [],
        "infrastructure-telemetry": [{**common, "id": "telemetry-1"}],
        "social-posts": [],
        "satellite-change-results": [],
    }
    operational = OperationalStub()
    projection = DashboardProjectionService(
        operational_service=operational,
        scenario_service=ScenarioWithGenericRiskMilestoneStub(),
        social_service=SocialStub(),
        building_service=BuildingStub(),
        materialized_state=MaterializedStub(rows),
        topics={
            "ai": "ai-analysis-results",
            "telemetry": "infrastructure-telemetry",
            "social": "social-posts",
            "satellite": "satellite-change-results",
        },
        map_config_loader=lambda: {"scenario": {"id": "scenario-1"}},
    )

    projection.snapshot()

    assert operational.include_risk is True
