"""Characterization tests for the pre-restructure dashboard contract.

These tests intentionally describe the dashboard's public surface rather than
its visual styling. A later information-architecture change may update the
expected navigation, but it must do so deliberately and preserve the recorded
capabilities and API response fields.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi import HTTPException

from dashboard import server


ROOT = Path(__file__).parents[1]
INDEX_HTML = ROOT / "dashboard" / "static" / "index.html"
APP_JS = ROOT / "dashboard" / "static" / "app.js"
AI_CSS = ROOT / "dashboard" / "static" / "ai.css"
PROCESS_CSS = ROOT / "dashboard" / "static" / "process-controls.css"


def _html() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _javascript() -> str:
    modules = sorted((APP_JS.parent / "modules").glob("*.js"))
    return "\n".join(path.read_text(encoding="utf-8") for path in [APP_JS, *modules])


def _ai_css() -> str:
    return AI_CSS.read_text(encoding="utf-8")


def _process_css() -> str:
    return PROCESS_CSS.read_text(encoding="utf-8")


def test_navigation_views_and_capabilities_are_recorded():
    html = _html()
    primary_navigation = re.findall(
        r'<button class="nav(?: active)?" data-view="([^"]+)"', html
    )
    all_navigation = re.findall(r'<button class="[^"]*nav[^"]*" data-view="([^"]+)"', html)
    views = re.findall(r'<section class="view(?: active)?" id="([^"]+)"', html)

    expected_views = [
        "operations",
        "overview",
        "pipeline",
        "diagnostics",
        "spark",
        "ai",
        "satellite-change",
        "buildings",
        "social-alerts",
        "media",
        "logs",
    ]
    assert primary_navigation == ["operations", "system-control"]
    assert all_navigation == [
        "operations",
        "system-control",
        "ai",
        "satellite-change",
        "social-alerts",
        "buildings",
        "diagnostics",
        "spark",
        "media",
        "logs",
    ]
    assert views == expected_views
    assert '<button class="nav active" data-view="operations">' in html
    assert '<section class="view active" id="operations">' in html
    assert "view === 'system-control'" in _javascript()
    assert '>Evidence</p>' in html
    assert '>Diagnostics</p>' in html
    assert 'href="/api/docs"' in html


def test_operational_map_layers_and_incident_sections_are_recorded():
    html = _html()
    javascript = _javascript()

    assert re.findall(r'data-map-layer="([^"]+)"', html) == [
        "satellite",
        "structural",
        "damage",
        "risk",
        "hotspots",
    ]
    for source in ("structural", "risk", "satellite-change"):
        assert f"addSource('{source}'" in javascript
    assert "['damage', 'damage-observations'" in javascript
    assert "['hotspots', 'confirmed-hotspots'" in javascript
    for layer in (
        "structural-buildings",
        "structural-roads",
        "risk-nodes",
        "damage-observations",
        "confirmed-hotspots",
        "satellite-change-cells",
    ):
        assert layer in javascript

    ordered_ids = [
        'id="operationsMap"',
        'id="mapSatelliteSummary"',
        'id="topRiskRows"',
        'id="opsPendingRows"',
        'id="timelineTrack"',
        'id="provenanceGrid"',
        'id="opsBuildingComponentHost"',
    ]
    assert [html.index(item) for item in ordered_ids] == sorted(
        html.index(item) for item in ordered_ids
    )


def test_scenario_controls_and_frontend_api_dependencies_are_recorded():
    html = _html()
    javascript = _javascript()

    assert re.findall(r'data-scenario-action="([^"]+)"', html) == [
        "start",
        "pause",
        "resume",
        "reset",
    ]
    assert 'id="scenarioSpeed"' in html
    assert 'id="systemScenarioSpeed"' in html
    assert re.findall(r'data-system-scenario-action="([^"]+)"', html) == [
        "start",
        "pause",
        "resume",
        "reset",
    ]
    assert "controlScenario('speed')" in javascript
    for path in (
        "/api/status",
        "/api/operations",
        "/api/social/alerts",
        "/api/satellite/change",
        "/api/scenario",
        "/api/buildings",
        "/api/objects",
    ):
        assert path in javascript
    assert "/api/operations/snapshot" in javascript
    assert "Promise.all([api('/api/operations')" not in javascript


def test_loading_empty_and_error_state_markers_are_recorded():
    html = _html()
    javascript = _javascript()

    required_html_states = {
        "operationsLoading",
        "operationsError",
        "mapEmpty",
        "sparkAlert",
        "sparkEmpty",
        "aiAlert",
        "aiEmpty",
        "satelliteChangeAlert",
        "satelliteChangeEmpty",
        "socialAlertError",
        "previewPlaceholder",
    }
    for element_id in required_html_states:
        assert f'id="{element_id}"' in html
        assert f"#{element_id}" in javascript

    assert "No TGNN scores available" in javascript
    assert "No reports awaiting review" in javascript
    assert "No Spark output yet" in html
    assert "No AI results yet" in html
    assert "No broad-area result yet" in html
    assert "No objects found" in javascript
    assert "Operational picture could not refresh" in javascript
    assert "Last successful data remains visible" in javascript


def test_satellite_summary_and_detail_are_consolidated():
    html = _html()
    javascript = _javascript()
    operations = html[html.index('id="operations"'):html.index('id="overview"')]
    detail = html[html.index('id="satellite-change"'):html.index('id="buildings"')]

    assert 'id="mapSatelliteSummary"' in operations
    assert 'id="mapSatelliteCounts"' in operations
    assert 'id="mapSatelliteProvenance"' in operations
    assert "data-satellite-drilldown" in operations
    assert 'id="opsSatellitePre"' not in operations
    assert 'id="opsSatellitePost"' not in operations
    for element_id in (
        "satellitePreImage", "satellitePostImage", "changeGrid",
        "unknownCellCount", "satelliteFootprint", "satelliteSourceProvenance",
        "satelliteReferenceDetail", "satelliteNormalization",
    ):
        assert f'id="{element_id}"' in detail
    assert "function adaptSatelliteEvidence" in javascript
    assert "renderOperationalSatelliteEvidence" in javascript
    assert "renderDetailedSatelliteEvidence" in javascript
    assert "'satellite-change': loadSatelliteChange" in javascript
    assert "data-satellite-drilldown" in javascript


def test_social_decisions_and_geography_have_single_implementations():
    html = _html()
    javascript = _javascript()

    assert 'id="socialHotspotMap"' not in html
    assert "renderSocialHotspots" not in javascript
    assert "-90.0855307" not in javascript
    assert "[data-alert-action][data-alert-id]" in javascript
    assert "window.reviewSocialAlert" not in javascript
    assert "window.reviewOperationalAlert" not in javascript
    assert "renderPendingAlerts('#opsPendingRows', '#opsPendingTotal'" in javascript
    assert "renderPendingAlerts('#pendingAlertRows', '#pendingAlertTotal'" in javascript
    assert "requestRefresh({includeHealth: false, reason: 'action'})" in javascript
    for element_id in (
        "socialScope", "responderIdentity", "reviewedAlertRows",
        "duplicateAlertRows", "contradictionAlertRows",
    ):
        assert f'id="{element_id}"' in html


def test_building_classification_uses_one_component_and_save_path():
    html = _html()
    javascript = _javascript()

    assert html.count('id="buildingClassificationComponent"') == 1
    assert html.count('id="saveBuildingComponent"') == 1
    for removed_id in (
        "opsBuildingSelect", "opsBuildingType", "opsSaveBuilding",
        "buildingSelect", "saveBuildingClassification",
    ):
        assert f'id="{removed_id}"' not in html
    for element_id in (
        "opsBuildingComponentHost", "inventoryBuildingComponentHost",
        "buildingSourceFilter", "buildingTypeFilter", "buildingStatusFilter",
        "buildingComponentCurrent", "buildingHistoryDetails",
        "buildingComponentHistory",
    ):
        assert f'id="{element_id}"' in html
    assert javascript.count("/classification`") == 1
    assert "function mount(context)" in javascript
    assert "function applyLocally" in javascript
    assert "assigned_building_type: record.assigned_type" in javascript
    assert "setMapData('structural', structural)" in javascript
    assert "setMapData('risk', risk)" in javascript


def test_tgnn_presentation_cannot_be_read_as_a_calibrated_measure():
    html = _html()
    javascript = _javascript()
    risk_panel_start = html.index("02 · TGNN RISK")
    risk_panel_end = html.index("03 · RESPONDER QUEUE")
    risk_panel = html[risk_panel_start:risk_panel_end].lower()
    renderer_start = javascript.index("const relativeRiskScore")
    renderer_end = javascript.index("function renderProvenance", renderer_start)
    renderer = javascript[renderer_start:renderer_end].lower()

    assert "relative risk score" in risk_panel
    assert "uncalibrated" in risk_panel
    assert 'role="tooltip"' in risk_panel
    assert 'aria-describedby="riskExplanation"' in html[risk_panel_start:risk_panel_end]
    assert "probability" not in risk_panel
    assert "%" not in risk_panel
    assert "probability" not in renderer
    assert "%" not in renderer
    assert "highest because" not in javascript.lower()
    assert ".why" not in renderer
    assert "current damage" in renderer
    assert "current stress" in renderer
    assert "graph context" in renderer
    assert "calibration warning" in renderer


def test_damage_analysis_separates_evaluation_from_live_inference():
    html = _html()
    javascript = _javascript()
    ai_view = html[html.index('id="ai"'):html.index('id="satellite-change"')]

    held_out = ai_view.index("PRIMARY OFFLINE EVALUATION")
    training = ai_view.index("TRAINING-RUN METRICS")
    live = ai_view.index("LIVE SCENARIO INFERENCE")
    assert held_out < training < live
    assert "Held-out test metrics" in ai_view
    assert "not live scenario accuracy" in ai_view
    assert "Inference outcomes, not evaluation accuracy" in ai_view
    assert "held_out_test_metrics" in javascript
    assert "final_metrics" in javascript
    assert 'id="aiDataset"' in ai_view
    assert 'id="aiCheckpointHash"' in ai_view
    assert 'id="heldOutDatasetSize"' in ai_view
    assert 'id="aiResultCards"' in ai_view
    assert 'id="aiHead"' not in ai_view
    assert 'id="aiBody"' not in ai_view


def test_damage_analysis_cards_support_filters_missing_images_and_error_states():
    html = _html()
    javascript = _javascript()
    css = _ai_css()

    for element_id in (
        "aiClassFilter",
        "aiTargetFilter",
        "aiConfidenceFilter",
        "aiStatusFilter",
        "aiAlert",
        "aiEmpty",
    ):
        assert f'id="{element_id}"' in html
        assert element_id in javascript
    for label in (
        "Target association",
        "GPS provenance",
        "Processing status",
        "Developer: raw event JSON",
        "Image unavailable",
    ):
        assert label in javascript
    assert "image_preview_url" in javascript
    assert "Damage analysis could not refresh" in javascript
    for status in ("duplicate_skipped", "filtered", "error"):
        assert f"status-{status}" in css


def test_refresh_controller_is_active_view_scoped_and_visibility_aware():
    html = _html()
    javascript = _javascript()

    assert "<title>DisasterScope — Operational picture</title>" in html
    assert '<h1 id="pageTitle">Operational picture</h1>' in html
    assert 'id="autoRefreshToggle"' in html
    assert 'id="refreshInterval"' in html
    assert 'id="refreshState"' in html
    assert 'title="Refresh active view"' in html
    for element_id in (
        "sourceEventTime",
        "graphFreshness",
        "riskFreshness",
        "lastUpdated",
    ):
        assert f'id="{element_id}"' in html
    assert "Source event time" in html
    assert "Graph snapshot time" in html
    assert "TGNN inference time" in html
    assert "UI refreshed" in javascript

    assert "const viewLoaders" in javascript
    for registration in (
        "operations: loadOperational",
        "spark: loadSpark",
        "ai: loadAI",
        "'satellite-change': loadSatelliteChange",
        "buildings: loadBuildings",
        "media: loadObjects",
        "logs: loadLogs",
    ):
        assert registration in javascript
    assert "installPerViewRefreshControls" in javascript
    assert "document.addEventListener('visibilitychange'" in javascript
    assert "Paused · tab hidden" in javascript
    assert "new AbortController()" in javascript
    assert "Request timed out after" in javascript
    assert "Promise.allSettled(work)" in javascript
    assert "Stale · refresh failed" in javascript
    assert "setInterval(" not in javascript
    assert "loadOperational();if($('#spark')" not in javascript


def test_global_status_presents_truthful_readiness_groups():
    html = _html()
    javascript = _javascript()
    css = _process_css()

    for element_id in ("overallReadiness", "readinessMessage", "readinessGrid"):
        assert f'id="{element_id}"' in html
        assert f"#{element_id}" in javascript
    for label in (
        "Infrastructure health",
        "Processing readiness",
        "Source readiness",
        "Scenario validation",
        "Dashboard freshness",
    ):
        assert label in javascript
    for state in ("healthy", "degraded", "stopped", "unavailable", "unknown"):
        assert f".readiness-pill.{state}" in css
    assert "can_run_scenario" in javascript
    assert "All core services connected" not in javascript
    assert "observation: ['Observation consumer'" in javascript


def test_remote_dashboard_exposes_observer_and_operator_modes_safely():
    html = _html()
    javascript = _javascript()

    for element_id in (
        "accessMode",
        "operatorLogin",
        "operatorToken",
        "operatorLoginButton",
        "operatorLogoutButton",
    ):
        assert f'id="{element_id}"' in html
        assert f"#{element_id}" in javascript
    assert "Read-only observer" in javascript
    assert "Authenticated operator" in javascript
    assert "X-Dashboard-Request" in javascript
    assert "DisasterScope-Operator" in javascript
    assert "sessionStorage" not in javascript
    assert "localStorage" not in javascript
    assert html.count("data-local-only") == 2
    assert "location.hostname" in javascript
    assert "applyOperatorControls" in javascript


def test_fastapi_route_surface_is_recorded():
    routes = {
        (route.path, method)
        for route in server.app.routes
        for method in (getattr(route, "methods", None) or set())
    }
    expected = {
        ("/api/status", "GET"),
        ("/api/access", "GET"),
        ("/api/infrastructure/{action}", "POST"),
        ("/api/process/{name}/start", "POST"),
        ("/api/process/{name}/stop", "POST"),
        ("/api/logs/{name}", "GET"),
        ("/api/spark/{source}", "GET"),
        ("/api/ai", "GET"),
        ("/api/satellite/change", "GET"),
        ("/api/social/alerts", "GET"),
        ("/api/social/alerts/{alert_id}/{action}", "POST"),
        ("/api/buildings", "GET"),
        ("/api/buildings/{building_source_id}", "GET"),
        ("/api/buildings/{building_source_id}/classification", "POST"),
        ("/api/objects", "GET"),
        ("/api/object", "GET"),
        ("/api/map/config", "GET"),
        ("/api/operations", "GET"),
        ("/api/operations/snapshot", "GET"),
        ("/api/scenario", "GET"),
        ("/api/scenario/{action}", "POST"),
    }
    assert expected <= routes
    assert any(
        route.path in {"", "/"} and route.name == "dashboard"
        for route in server.app.routes
    )


def test_status_ai_spark_and_object_response_envelopes(monkeypatch):
    server.health_cache.clear()
    server.topic_tails.clear()
    server.detail_cache.clear()
    monkeypatch.setattr(
        server, "docker_services", lambda: ([{"Service": "kafka"}], None)
    )
    monkeypatch.setattr(server, "kafka_counts", lambda: ({"drone-video": 3}, None))
    monkeypatch.setattr(
        server,
        "minio_health_summary",
        lambda: ({"drone": {"objects": 1, "bytes": None}}, None),
    )
    monkeypatch.setattr(server, "dedup_summary", lambda: ({"drone": 1}, None))
    monkeypatch.setattr(
        server, "process_states", lambda: {"spark": {"running": True}}
    )

    status = server.status()
    assert set(status) == {
        "configuration",
        "docker",
        "kafka",
        "minio",
        "dedup",
        "processes",
        "readiness",
        "errors",
        "diagnostics",
    }
    assert set(status["configuration"]) == {
        "kafka",
        "minio",
        "transfer_mode",
        "spark_offsets",
        "ai_model",
    }
    assert set(status["errors"]) == {"docker", "kafka", "minio", "dedup"}
    assert set(status["readiness"]) == {
        "overall",
        "infrastructure",
        "processing",
        "sources",
        "scenario",
        "freshness",
    }

    monkeypatch.setattr(
        server, "ai_model_summary", lambda: ({"model_available": True}, None)
    )
    monkeypatch.setattr(
        server, "recent_topic_events", lambda topic, limit: ([{"frame_id": "f1"}], None)
    )
    assert set(server.ai_output()) == {"model", "results", "errors", "diagnostics"}

    monkeypatch.setattr(
        server, "parquet_preview", lambda source: ([{"id": "row-1"}], 2, None)
    )
    spark = server.spark_output("drone")
    assert spark["source"] == "drone"
    assert spark["rows"] == [{"id": "row-1"}]
    assert spark["files"] == 2
    assert spark["error"] is None
    assert spark["diagnostics"]["parquet_cache"]["cache_hit"] is False

    monkeypatch.setattr(
        server,
        "minio_objects",
        lambda limit=30: (
            {"drone": {"objects": 1, "bytes": 20}},
            [{"key": "drone/aa/image.jpg"}],
            None,
        ),
    )
    assert set(server.objects()) == {"summary", "objects", "error", "diagnostics"}


def test_ai_output_adds_safe_minio_previews_and_preserves_missing_images(monkeypatch):
    rows = [
        {
            "frame_id": "with-preview",
            "object_key": "drone/aa/evidence.jpg",
            "detections": [],
        },
        {
            "frame_id": "missing-preview",
            "content_hash": "not-indexed",
            "detections": [],
        },
        {
            "frame_id": "unsafe-preview",
            "object_key": "satellite/aa/evidence.tif",
            "detections": [],
        },
    ]
    monkeypatch.setattr(server, "ai_model_summary", lambda: ({}, None))
    monkeypatch.setattr(
        server.topic_tails,
        "get",
        lambda topic, limit: (rows, None, {"cache_hit": False}),
    )
    monkeypatch.setattr(server, "object_key_for_content_hash", lambda *_: None)

    payload = server.ai_output()

    assert payload["results"][0]["image_preview_url"] == (
        "/api/object?key=drone/aa/evidence.jpg"
    )
    assert payload["results"][1]["image_preview_url"] is None
    assert payload["results"][2]["image_preview_url"] is None


def test_routine_status_uses_cached_lightweight_health_checks(monkeypatch):
    server.health_cache.clear()
    calls = {"docker": 0, "kafka": 0, "minio": 0}

    def loaded(name, value):
        def load():
            calls[name] += 1
            return value, None
        return load

    monkeypatch.setattr(server, "docker_services", loaded("docker", []))
    monkeypatch.setattr(server, "kafka_counts", loaded("kafka", {}))
    monkeypatch.setattr(server, "minio_health_summary", loaded("minio", {}))
    monkeypatch.setattr(
        server,
        "minio_objects",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("routine status must not enumerate MinIO objects")
        ),
    )
    monkeypatch.setattr(server, "dedup_summary", lambda: ({}, None))
    monkeypatch.setattr(server, "process_states", lambda: {})

    first = server.status()
    second = server.status()

    assert calls == {"docker": 1, "kafka": 1, "minio": 1}
    assert first["diagnostics"]["health_cache"]["minio"]["cache_hit"] is False
    assert second["diagnostics"]["health_cache"]["minio"]["cache_hit"] is True


def test_operational_social_satellite_and_building_response_envelopes(monkeypatch):
    server.topic_tails.clear()
    event_reads = {
        "ai-analysis-results": ([{"frame_id": "frame-1"}], None),
        "infrastructure-telemetry": ([{"id": "telemetry-1"}], None),
        "satellite-change-results": (
            [
                {
                    "scenario_id": "louisiana-east-flood-v1",
                    "source_images": {
                        "pre": {"object_key": "satellite/aa/pre.jpg"},
                        "post": {"object_key": "satellite/bb/post.jpg"},
                    },
                }
            ],
            None,
        ),
        "social-posts": ([{"id": "social-1"}], None),
    }
    monkeypatch.setattr(
        server, "recent_topic_events", lambda topic, limit: event_reads[topic]
    )
    class MaterializedStub:
        @staticmethod
        def refresh():
            return {
                "sources": {
                    topic: {"error": error}
                    for topic, (_, error) in event_reads.items()
                }
            }

        @staticmethod
        def events(topic, **kwargs):
            return event_reads[topic][0]

        @staticmethod
        def reviews(**kwargs):
            return []

    monkeypatch.setattr(server, "materialized_state", MaterializedStub())
    monkeypatch.setattr(server, "_scenario_rows_visible_at_current_time", lambda rows: rows)

    captured = {}

    class OperationalStub:
        scenario = {"scenario_id": "louisiana-east-flood-v1"}

        @staticmethod
        def snapshot(ai_events, telemetry_events):
            captured["ai"] = ai_events
            captured["telemetry"] = telemetry_events
            return {"layers": {}, "errors": []}

    monkeypatch.setattr(server, "operational_dashboard", OperationalStub())
    operational = server.operational_view()
    assert captured == {
        "ai": [{"frame_id": "frame-1"}],
        "telemetry": [{"id": "telemetry-1"}],
    }
    assert set(operational["source_errors"]) == {
        "ai_analysis_results",
        "infrastructure_telemetry",
    }

    class SocialStub:
        ingested = None

        def rebuild(self, rows, reviews):
            self.ingested = rows

        @staticmethod
        def state():
            return {
                "counts": {},
                "pending_alerts": [],
                "confirmed_hotspots": {"type": "FeatureCollection", "features": []},
            }

    social_stub = SocialStub()
    monkeypatch.setattr(server, "social_alerts", social_stub)
    social = server.social_alert_state(False)
    assert social_stub.ingested == [{"id": "social-1"}]
    assert {"counts", "pending_alerts", "confirmed_hotspots", "kafka_error"} <= set(
        social
    )

    satellite = server.satellite_change_output()
    assert set(satellite) == {"latest", "result_count", "error", "diagnostics"}
    assert satellite["latest"]["source_images"]["pre"]["preview_url"].startswith(
        "/api/object?key=satellite/"
    )

    class BuildingStub:
        @staticmethod
        def list_buildings():
            return {
                "type": "FeatureCollection",
                "features": [
                    {"properties": {"classification": {"assigned_type": "shelter"}}}
                ],
            }

        @staticmethod
        def get_building(source_id):
            return {"building_source_id": source_id, "revision_history": []}

    monkeypatch.setattr(server, "building_classifications", BuildingStub())
    listing = server.buildings()
    assert set(listing) == {
        "buildings",
        "count",
        "classified_count",
        "raw_gis_mutated",
    }
    assert listing["classified_count"] == 1
    assert server.building("osm-building-1")["building_source_id"] == "osm-building-1"


class _DecisionServiceStub:
    def __init__(self, *, owns_alerts=True):
        self.decisions = []
        self.reset_count = 0
        self.owns_alerts = owns_alerts

    def has_alert(self, alert_id):
        return self.owns_alerts

    def decide(self, alert_id, action, responder_id):
        self.decisions.append((alert_id, action, responder_id))
        return {
            "alert_id": alert_id,
            "status": "CONFIRMED" if action == "confirm" else "REPORTED_FALSE",
        }

    @staticmethod
    def state():
        return {"pending_alerts": []}

    def reset(self):
        self.reset_count += 1


def test_dashboard_social_decision_and_scenario_control_envelopes(monkeypatch):
    class MaterializedStub:
        reviews = []

        def record_review(self, **review):
            self.reviews.append(review)

        @staticmethod
        def refresh(**kwargs):
            return {"sources": {}}

        @staticmethod
        def reset_replay(scenario_id):
            return 1

    monkeypatch.setattr(server, "materialized_state", MaterializedStub())
    social = _DecisionServiceStub()
    scenario_social = _DecisionServiceStub(owns_alerts=False)
    monkeypatch.setattr(server, "social_alerts", social)
    monkeypatch.setattr(server, "scenario_social_alerts", scenario_social)

    confirmed = server.review_social_alert(
        "social-1", "confirm", server.ResponderDecision(responder_id="operator-a")
    )
    rejected = server.review_social_alert(
        "social-2", "reject", server.ResponderDecision(responder_id="operator-b")
    )
    assert confirmed["alert"]["status"] == "CONFIRMED"
    assert rejected["alert"]["status"] == "REPORTED_FALSE"
    assert social.decisions == [
        ("social-1", "confirm", "operator-a"),
        ("social-2", "reject", "operator-b"),
    ]
    assert set(confirmed) == {"ok", "message", "alert", "state"}

    class ScenarioStub:
        def __init__(self):
            self.calls = []

        def control(self, action, speed):
            self.calls.append((action, speed))
            return {"state": "idle", "speed": speed or 10}

        @staticmethod
        def status():
            return {"state": "idle", "upcoming_events": []}

    scenario = ScenarioStub()
    monkeypatch.setattr(server, "scenario_runner", scenario)
    assert server.scenario_status()["state"] == "idle"
    response = server.scenario_control("speed", server.ScenarioControlRequest(speed=20))
    assert response == {
        "ok": True,
        "message": "Scenario speed accepted",
        "status": {"state": "idle", "speed": 20.0},
    }
    server.scenario_control("reset", server.ScenarioControlRequest())
    assert scenario.calls == [("speed", 20.0), ("reset", None)]
    assert social.reset_count == 0
    assert scenario_social.reset_count == 1


def test_dashboard_rejects_ambiguous_cross_scope_alert_identity(monkeypatch):
    monkeypatch.setattr(server, "social_alerts", _DecisionServiceStub())
    monkeypatch.setattr(server, "scenario_social_alerts", _DecisionServiceStub())

    with pytest.raises(HTTPException) as error:
        server.review_social_alert(
            "shared-alert-id",
            "confirm",
            server.ResponderDecision(responder_id="operator-a"),
        )

    assert error.value.status_code == 409
    assert "both scenario and general scopes" in error.value.detail


class _ProcessManagerStub:
    def __init__(self):
        self.started = []
        self.stopped = []

    def start(self, name, command):
        self.started.append((name, command))
        return True, f"Started {name}."

    def stop(self, name):
        self.stopped.append(name)
        return True, f"Stopped {name}."

    @staticmethod
    def status(name):
        return {"running": True, "pid": 1234, "exit_code": None, "name": name}

    @staticmethod
    def log_tail(name, lines):
        return f"{name}:{lines}"


def test_process_and_infrastructure_control_envelopes(monkeypatch):
    manager = _ProcessManagerStub()
    monkeypatch.setattr(server, "manager", manager)
    monkeypatch.setattr(server, "spark_command", lambda: ["spark-submit", "stream.py"])
    monkeypatch.setattr(
        server, "python_module", lambda module, *args: ["python", "-m", module, *args]
    )

    spark = server.start_process("spark", server.StartOptions(limit=5))
    ai = server.start_process("ai", server.StartOptions(limit=5))
    diagnostic_ai = server.start_process(
        "ai", server.StartOptions(limit=5, max_events=3)
    )
    satellite_worker = server.start_process(
        "satellite_change", server.StartOptions(limit=5)
    )
    diagnostic_satellite = server.start_process(
        "satellite_change", server.StartOptions(max_events=4)
    )
    producer = server.start_process(
        "drone", server.StartOptions(limit=7, delay=0.25)
    )
    observation = server.start_process(
        "observation", server.StartOptions(limit=5)
    )
    assert set(spark) == {"ok", "message", "status"}
    assert manager.started[0] == ("spark", ["spark-submit", "stream.py"])
    assert "ai.computer_vision.worker" in manager.started[1][1]
    assert "--limit" not in manager.started[1][1]
    assert manager.started[2][1][-2:] == ["--limit", "3"]
    assert "--limit" not in manager.started[3][1]
    assert manager.started[4][1][-2:] == ["--limit", "4"]
    assert "data_source.drone.drone_producer" in manager.started[5][1]
    assert manager.started[5][1][-4:] == ["--limit", "7", "--delay", "0.25"]
    assert "consumers.observation_consumer" in manager.started[6][1]
    assert "--allow-scenario-simulated-gps" in manager.started[6][1]
    assert "--limit" not in manager.started[6][1]
    assert ai["status"]["running"] is True
    assert diagnostic_ai["status"]["running"] is True
    assert observation["status"]["running"] is True
    assert satellite_worker["status"]["running"] is True
    assert diagnostic_satellite["status"]["running"] is True
    assert producer["status"]["pid"] == 1234

    stopped = server.stop_process("drone")
    assert stopped["ok"] is True
    assert manager.stopped == ["drone"]
    assert server.logs("spark", 25) == {"name": "spark", "log": "spark:25"}

    with pytest.raises(HTTPException) as error:
        server.start_process("unknown", server.StartOptions())
    assert error.value.status_code == 404

    commands = []
    monkeypatch.setattr(
        server,
        "run_command",
        lambda command, timeout: (commands.append((command, timeout)) or (True, "ok")),
    )
    assert server.infrastructure("start") == {"ok": True, "message": "ok"}
    assert server.infrastructure("stop") == {"ok": True, "message": "ok"}
    assert commands == [
        (["docker", "compose", "--env-file", ".env", "up", "-d"], 120),
        (["docker", "compose", "stop"], 120),
    ]
