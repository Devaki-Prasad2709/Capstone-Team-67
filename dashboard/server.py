"""FastAPI backend for the local disaster streaming control center."""

from __future__ import annotations

from dashboard.map_api import map_config
from dashboard.social_alert_service import SocialAlertService
from dashboard.building_classification_service import BuildingClassificationService
from dashboard.operational_service import OperationalDashboardService
from dashboard.scenario_service import DashboardScenarioService
from scenario_runtime import ScenarioRegistry
from dashboard.cache_service import HealthCache, TopicTailCache
from dashboard.projection_service import DashboardProjectionService
from dashboard.materialized_state import DashboardMaterializedState
from dashboard.readiness import (
    freshness_readiness,
    infrastructure_readiness,
    overall_readiness,
    processing_readiness,
    source_readiness,
)

from datetime import datetime, timezone
from pathlib import Path
import logging
import time
from threading import RLock
from typing import Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.settings import settings
from dashboard.process_manager import ProcessManager, python_module, spark_command
from dashboard.security import operator_access, require_operator
from dashboard.services import (
    dedup_summary,
    ai_model_summary,
    docker_services,
    kafka_counts,
    incremental_topic_events,
    minio_health_summary,
    minio_objects,
    object_key_for_content_hash,
    object_client,
    parquet_preview,
    recent_topic_events,
    run_command,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
logger = logging.getLogger(__name__)
PROCESS_NAMES = (
    "spark", "ai", "satellite_change", "observation", "social", "drone", "satellite", "gis"
)
PROCESSING_WORKERS = ("spark", "ai", "satellite_change", "observation")
FINITE_PRODUCERS = ("social", "drone", "satellite", "gis")

app = FastAPI(title="Disaster Streaming Control Center", docs_url="/api/docs")
manager = ProcessManager()
scenario_registry = ScenarioRegistry()
active_registration = scenario_registry.get()
scenario_context_lock = RLock()
social_alerts = SocialAlertService()
scenario_social_alerts = SocialAlertService(active_registration.gis_path)
building_classifications = BuildingClassificationService(active_registration.gis_path)
operational_dashboard = OperationalDashboardService(
    building_classifications,
    gis_path=active_registration.gis_path,
    scenario_path=active_registration.manifest_path,
)
scenario_runner = DashboardScenarioService(active_registration.manifest_path)
topic_tails = TopicTailCache(
    lambda topic, limit: recent_topic_events(topic, limit), ttl_seconds=10.0
)
health_cache = HealthCache(ttl_seconds=10.0)
readiness_cache = HealthCache(ttl_seconds=60.0)
detail_cache = HealthCache(ttl_seconds=10.0)
DASHBOARD_TOPICS = {
    "ai": "ai-analysis-results",
    "telemetry": "infrastructure-telemetry",
    "social": "social-posts",
    "satellite": "satellite-change-results",
}
materialized_state = DashboardMaterializedState(
    lambda topic, checkpoints: incremental_topic_events(topic, checkpoints),
    DASHBOARD_TOPICS,
)
dashboard_projection = DashboardProjectionService(
    operational_service=operational_dashboard,
    scenario_service=scenario_runner,
    social_service=scenario_social_alerts,
    building_service=building_classifications,
    materialized_state=materialized_state,
    topics=DASHBOARD_TOPICS,
    map_config_loader=lambda: map_config(active_registration.manifest_path, active_registration.gis_path),
    object_key_resolver=object_key_for_content_hash,
)
last_successful_updates: dict[str, str | None] = {
    "kafka": None,
    "minio": None,
    "tgnn": None,
}


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled dashboard request failure: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "The dashboard request failed. Check local server logs."})


class StartOptions(BaseModel):
    limit: int = Field(default=20, ge=1, le=10000)
    delay: float | None = Field(default=None, ge=0, le=60)
    max_events: int | None = Field(default=None, ge=1, le=10000)


class ResponderDecision(BaseModel):
    responder_id: str = Field(min_length=1, max_length=200)


class BuildingClassificationRequest(BaseModel):
    assigned_type: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class ScenarioControlRequest(BaseModel):
    speed: float | None = Field(default=None, gt=0, le=100)


def _observation_command(registration=None) -> list[str]:
    """Build the scenario-bound observation worker command."""
    registration = registration or active_registration
    return python_module(
        "consumers.observation_consumer",
        "--gis-path",
        str(registration.gis_path),
        "--allow-scenario-simulated-gps",
        "--group-id",
        f"disaster-observation-dashboard-{registration.scenario_id}",
    )


def _classification_database(registration):
    if registration.scenario_id == scenario_registry.default_scenario_id:
        return settings.resolved_building_classification_database_path
    safe_id = registration.scenario_id.replace("/", "-").replace("\\", "-")
    return settings.project_root / "storage" / "gis" / f"building_classifications_{safe_id}.sqlite3"


def _activate_scenario_context(scenario_id: str) -> None:
    """Atomically replace every scenario-scoped service with one coherent context."""
    global active_registration, scenario_social_alerts, building_classifications
    global operational_dashboard, scenario_runner, dashboard_projection
    registration = scenario_registry.get(scenario_id)
    new_buildings = BuildingClassificationService(
        registration.gis_path,
        _classification_database(registration),
    )
    new_operational = OperationalDashboardService(
        new_buildings,
        gis_path=registration.gis_path,
        scenario_path=registration.manifest_path,
    )
    new_runner = DashboardScenarioService(registration.manifest_path)
    new_social = SocialAlertService(registration.gis_path)
    new_projection = DashboardProjectionService(
        operational_service=new_operational,
        scenario_service=new_runner,
        social_service=new_social,
        building_service=new_buildings,
        materialized_state=materialized_state,
        topics=DASHBOARD_TOPICS,
        map_config_loader=lambda: map_config(registration.manifest_path, registration.gis_path),
        object_key_resolver=object_key_for_content_hash,
    )
    with scenario_context_lock:
        active_registration = registration
        building_classifications = new_buildings
        operational_dashboard = new_operational
        scenario_runner = new_runner
        scenario_social_alerts = new_social
        dashboard_projection = new_projection
        topic_tails.clear()
        readiness_cache.clear()
        detail_cache.clear()


def _scenario_rows_visible_at_current_time(rows: list[dict]) -> list[dict]:
    """Project immutable Kafka history onto the runner's current replay time."""
    status = scenario_runner.status()
    if status["state"] == "idle":
        return []
    cutoff = datetime.fromisoformat(
        status["simulation_timestamp"].replace("Z", "+00:00")
    ).timestamp()
    visible = []
    for row in rows:
        value = row.get("scenario_timestamp", row.get("timestamp"))
        try:
            if isinstance(value, str):
                event_time = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if event_time.tzinfo is None:
                    event_time = event_time.replace(tzinfo=timezone.utc)
                event_epoch = event_time.timestamp()
            else:
                event_epoch = float(value)
        except (TypeError, ValueError):
            continue
        if event_epoch <= cutoff:
            visible.append(row)
    return visible


def process_states() -> dict[str, dict[str, object]]:
    return {name: manager.status(name) for name in PROCESS_NAMES}


def _public_process_states(states: dict[str, dict[str, object]]) -> dict[str, dict[str, object]]:
    """Retain actionable lifecycle state without exposing local exception detail."""
    result = {}
    for name, value in states.items():
        public = dict(value)
        if public.get("last_failure"):
            public["last_failure"] = "Process failed; inspect local dashboard logs."
        result[name] = public
    return result


def _scenario_readiness_check() -> tuple[dict[str, object], str | None]:
    try:
        checker = getattr(scenario_runner, "readiness", None)
        if checker is None:
            return {
                "state": "unknown",
                "valid": False,
                "message": "Scenario validator is unavailable.",
                "action": "Restore the dashboard scenario service.",
            }, None
        return checker(), None
    except Exception as exc:
        return {
            "state": "unknown",
            "valid": False,
            "message": f"Scenario readiness could not be checked: {exc}",
            "action": "Inspect the scenario validation logs and retry.",
        }, str(exc)


@app.get("/api/status")
def status() -> dict[str, object]:
    started = time.perf_counter()
    docker, docker_error, docker_cache = health_cache.get("docker", docker_services)
    kafka, kafka_error, kafka_cache = health_cache.get("kafka", kafka_counts)
    minio, minio_error, minio_cache = health_cache.get(
        "minio", minio_health_summary
    )
    scenario_ready, scenario_error, scenario_cache = readiness_cache.get(
        "scenario_readiness", _scenario_readiness_check
    )
    dedup, dedup_error = dedup_summary()
    for source, error in (
        ("docker", docker_error),
        ("kafka", kafka_error),
        ("minio", minio_error),
        ("dedup", dedup_error),
        ("scenario", scenario_error),
    ):
        if error:
            logger.warning("Dashboard %s readiness check failed: %s", source, error)
    processes = _public_process_states(process_states())
    if not kafka_error:
        last_successful_updates["kafka"] = kafka_cache["cached_at"]
    if not minio_error:
        last_successful_updates["minio"] = minio_cache["cached_at"]
    projection_freshness = dashboard_projection.cached_freshness()
    if projection_freshness.get("tgnn_inference"):
        last_successful_updates["tgnn"] = projection_freshness["tgnn_inference"]
    infrastructure_ready = infrastructure_readiness(
        docker, docker_error, kafka_error, minio_error
    )
    sources_ready = source_readiness(
        {
            "SPACENET8_DATASET_PATH": (settings.spacenet8_dataset_path, True),
            "ISBDA_DATASET_PATH": (settings.isbda_dataset_path, True),
            "AI_MODEL_PATH": (str(settings.resolved_ai_model_path), True),
            "SOCIAL_DATASET_PATH": (settings.social_dataset_path, False),
            "DRONE_DATASET_PATH": (settings.drone_dataset_path, False),
            "SATELLITE_DATASET_PATH": (settings.satellite_dataset_path, False),
            "GIS_DATASET_PATH": (settings.gis_dataset_path, False),
        },
        processes,
    )
    processing_ready = processing_readiness(processes)
    freshness_ready = freshness_readiness(
        last_successful_updates,
        {"kafka": kafka_error, "minio": minio_error, "tgnn": None},
    )
    readiness_groups = {
        "infrastructure": infrastructure_ready,
        "processing": processing_ready,
        "sources": sources_ready,
        "scenario": scenario_ready,
        "freshness": freshness_ready,
    }
    readiness = {
        "overall": overall_readiness(readiness_groups),
        **readiness_groups,
    }
    return {
        "configuration": {
            "kafka": settings.kafka_bootstrap_servers,
            "minio": settings.object_storage_endpoint_url,
            "transfer_mode": settings.image_transfer_mode,
            "spark_offsets": settings.spark_starting_offsets,
            "ai_model": settings.resolved_ai_model_path.name,
        },
        "docker": docker,
        "kafka": kafka,
        "minio": minio,
        "dedup": dedup,
        "processes": processes,
        "readiness": readiness,
        "errors": {
            "docker": "Docker health check failed." if docker_error else None,
            "kafka": "Kafka health check failed." if kafka_error else None,
            "minio": "MinIO health check failed." if minio_error else None,
            "dedup": "Deduplication index check failed." if dedup_error else None,
        },
        "diagnostics": {
            "request_duration_ms": round((time.perf_counter() - started) * 1000, 3),
            "health_cache": {
                "docker": docker_cache,
                "kafka": kafka_cache,
                "minio": minio_cache,
                "scenario": scenario_cache,
            },
        },
    }


@app.get("/api/access")
def access_mode(request: Request) -> dict[str, object]:
    """Expose authorization mode without returning credentials or token material."""
    access = operator_access(request)
    return {
        **access,
        "minio_console_available": access["local_development"],
    }


@app.post("/api/infrastructure/{action}", dependencies=[Depends(require_operator)])
def infrastructure(action: Literal["start", "stop"]) -> dict[str, object]:
    command = (
        ["docker", "compose", "--env-file", ".env", "up", "-d"]
        if action == "start"
        else ["docker", "compose", "stop"]
    )
    ok, message = run_command(command, 120)
    health_cache.clear()
    if not ok:
        logger.error("Infrastructure %s failed: %s", action, message)
        verb = "started" if action == "start" else "stopped"
        message = f"Infrastructure could not be {verb}. Check local dashboard logs."
    return {"ok": ok, "message": message}


@app.post("/api/process/{name}/start", dependencies=[Depends(require_operator)])
def start_process(name: str, options: StartOptions) -> dict[str, object]:
    if name not in PROCESS_NAMES:
        raise HTTPException(404, "Unknown process")
    if name == "spark":
        command = spark_command()
    elif name == "ai":
        command = python_module("ai.computer_vision.worker")
    elif name == "satellite_change":
        command = python_module("core.satellite.change_worker")
    elif name == "observation":
        command = _observation_command()
    elif name in FINITE_PRODUCERS:
        defaults = {
            "social": settings.social_delay,
            "drone": settings.drone_delay,
            "satellite": settings.satellite_delay,
            "gis": settings.gis_delay,
        }
        delay = defaults[name] if options.delay is None else options.delay
        command = python_module(
            f"data_source.{name}.{name}_producer",
            "--limit",
            str(options.limit),
            "--delay",
            str(delay),
        )
    if name in PROCESSING_WORKERS[1:] and options.max_events is not None:
        command.extend(["--limit", str(options.max_events)])
    ok, message = manager.start(name, command)
    if not ok:
        logger.error("Could not start dashboard process %s: %s", name, message)
        message = f"{name} could not be started. Check process status and local logs."
    return {"ok": ok, "message": message, "status": manager.status(name)}


@app.post("/api/process/{name}/stop", dependencies=[Depends(require_operator)])
def stop_process(name: str) -> dict[str, object]:
    if name not in PROCESS_NAMES:
        raise HTTPException(404, "Unknown process")
    ok, message = manager.stop(name)
    if not ok:
        logger.error("Could not stop dashboard process %s: %s", name, message)
        message = f"{name} could not be stopped. Check process status and local logs."
    return {"ok": ok, "message": message, "status": manager.status(name)}


@app.get("/api/logs/{name}")
def logs(name: str, lines: int = Query(default=150, ge=20, le=1000)) -> dict[str, str]:
    if name not in PROCESS_NAMES:
        raise HTTPException(404, "Unknown process")
    return {"name": name, "log": manager.log_tail(name, lines)}


@app.get("/api/spark/{source}")
def spark_output(source: Literal["social", "drone", "satellite", "gis", "ai"]) -> dict[str, object]:
    def load():
        rows, files, error = parquet_preview(source)
        return {"rows": rows, "files": files}, error

    payload, error, cache = detail_cache.get(f"parquet:{source}", load)
    return {
        "source": source,
        **payload,
        "error": error,
        "diagnostics": {"parquet_cache": cache},
    }


@app.get("/api/ai")
def ai_output() -> dict[str, object]:
    model, model_error = ai_model_summary()
    rows, results_error, cache = topic_tails.get("ai-analysis-results", 100)
    for row in rows:
        detections = row.get("detections") or []
        image_reference = (
            detections[0].get("image_reference")
            if detections and isinstance(detections[0], dict)
            else {}
        ) or {}
        key = row.get("object_key") or image_reference.get("object_key")
        content_hash = row.get("content_hash") or image_reference.get("content_hash")
        if not key:
            key = object_key_for_content_hash("drone", content_hash)
        row["image_preview_url"] = (
            "/api/object?key=" + key
            if isinstance(key, str) and key.startswith("drone/")
            else None
        )
    return {
        "model": model,
        "results": rows,
        "errors": {"model": model_error, "results": results_error},
        "diagnostics": {"kafka_tail": cache},
    }


@app.get("/api/satellite/change")
def satellite_change_output() -> dict[str, object]:
    rows, error, cache = topic_tails.get("satellite-change-results", 10)
    scenario_id = operational_dashboard.scenario["scenario_id"]
    rows = [row for row in rows if row.get("scenario_id") == scenario_id]
    rows = _scenario_rows_visible_at_current_time(rows)
    latest = rows[0] if rows else None
    if latest:
        for image in (latest.get("source_images") or {}).values():
            key = image.get("object_key") if isinstance(image, dict) else None
            if isinstance(image, dict) and not key:
                key = object_key_for_content_hash("satellite", image.get("content_hash"))
            if isinstance(key, str) and key.startswith("satellite/"):
                image["preview_url"] = "/api/object?key=" + key
    return {
        "latest": latest,
        "result_count": len(rows),
        "error": error,
        "diagnostics": {"kafka_tail": cache},
    }


@app.get("/api/social/alerts")
def social_alert_state(scenario_only: bool = Query(False)) -> dict[str, object]:
    materialization = materialized_state.refresh()
    scenario_id = operational_dashboard.scenario["scenario_id"]
    if scenario_only:
        rows = materialized_state.events(
            DASHBOARD_TOPICS["social"],
            scenario_id=scenario_id,
            replay_only=True,
        )
        rows = _scenario_rows_visible_at_current_time(rows)
        service = scenario_social_alerts
        reviews = materialized_state.reviews(
            scope="scenario", scenario_id=scenario_id
        )
    else:
        rows = materialized_state.events(DASHBOARD_TOPICS["social"])
        rows = [row for row in rows if row.get("scenario_id") != scenario_id]
        service = social_alerts
        reviews = materialized_state.reviews(scope="production", scenario_id=None)
    service.rebuild(rows, reviews)
    state = service.state()
    state["scope"] = "scenario" if scenario_only else "general"
    state["scenario_id"] = scenario_id if scenario_only else None
    state["kafka_error"] = materialization["sources"][DASHBOARD_TOPICS["social"]]["error"]
    state["diagnostics"] = {"materialization": materialization}
    return state


def _hydrate_social_services() -> None:
    """Restore both review scopes before a direct post-restart decision request."""
    materialized_state.refresh()
    scenario_id = operational_dashboard.scenario["scenario_id"]
    scenario_rows = materialized_state.events(
        DASHBOARD_TOPICS["social"],
        scenario_id=scenario_id,
        replay_only=True,
    )
    scenario_rows = _scenario_rows_visible_at_current_time(scenario_rows)
    scenario_social_alerts.rebuild(
        scenario_rows,
        materialized_state.reviews(scope="scenario", scenario_id=scenario_id),
    )
    production_rows = [
        row
        for row in materialized_state.events(DASHBOARD_TOPICS["social"])
        if row.get("scenario_id") != scenario_id
    ]
    social_alerts.rebuild(
        production_rows,
        materialized_state.reviews(scope="production", scenario_id=None),
    )


@app.post("/api/social/alerts/{alert_id}/{action}", dependencies=[Depends(require_operator)])
def review_social_alert(
    alert_id: str,
    action: Literal["confirm", "reject", "report_false"],
    decision: ResponderDecision,
) -> dict[str, object]:
    owners = [
        service
        for service in (scenario_social_alerts, social_alerts)
        if service.has_alert(alert_id)
    ]
    if not owners:
        _hydrate_social_services()
        owners = [
            service
            for service in (scenario_social_alerts, social_alerts)
            if service.has_alert(alert_id)
        ]
    if not owners:
        raise HTTPException(404, "Unknown social alert")
    if len(owners) > 1:
        raise HTTPException(
            409,
            "Alert identity exists in both scenario and general scopes",
        )
    service = owners[0]
    try:
        alert = service.decide(alert_id, action, decision.responder_id)
    except KeyError as exc:
        raise HTTPException(404, "Unknown social alert") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    is_scenario = service is scenario_social_alerts
    materialized_state.record_review(
        scope="scenario" if is_scenario else "production",
        scenario_id=(operational_dashboard.scenario["scenario_id"] if is_scenario else None),
        alert_id=alert_id,
        action=action,
        responder_id=decision.responder_id,
    )
    dashboard_projection.invalidate()
    return {
        "ok": True,
        "message": f"Alert {alert_id} changed to {alert['status']}",
        "alert": alert,
        "state": service.state(),
    }


@app.get("/api/buildings")
def buildings() -> dict[str, object]:
    overlay = building_classifications.list_buildings()
    return {
        "buildings": overlay,
        "count": len(overlay["features"]),
        "classified_count": sum(
            feature["properties"]["classification"] is not None
            for feature in overlay["features"]
        ),
        "raw_gis_mutated": False,
    }


@app.get("/api/buildings/{building_source_id}")
def building(building_source_id: str) -> dict[str, object]:
    try:
        return building_classifications.get_building(building_source_id)
    except KeyError as exc:
        raise HTTPException(404, "Unknown GIS building source ID") from exc


@app.post("/api/buildings/{building_source_id}/classification", dependencies=[Depends(require_operator)])
def classify_building(
    building_source_id: str,
    assignment: BuildingClassificationRequest,
) -> dict[str, object]:
    try:
        result = building_classifications.classify(
            building_source_id,
            assignment.assigned_type,
            assignment.operator,
            assignment.notes,
        )
    except KeyError as exc:
        raise HTTPException(404, "Unknown GIS building source ID") from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    dashboard_projection.invalidate()
    return {
        "ok": True,
        "message": f"Classification revision saved for {building_source_id}",
        **result,
    }


@app.get("/api/objects")
def objects() -> dict[str, object]:
    def load():
        summary, rows, error = minio_objects()
        return {"summary": summary, "objects": rows}, error

    payload, error, cache = detail_cache.get("minio_objects", load)
    return {
        **payload,
        "error": error,
        "diagnostics": {"object_list_cache": cache},
    }


@app.get("/api/object")
def object_image(key: str = Query(min_length=1)) -> Response:
    if not key.startswith(("drone/", "satellite/", "gis/")):
        raise HTTPException(400, "Only pipeline image objects can be previewed")
    try:
        result = object_client().get_object(Bucket=settings.object_storage_bucket, Key=key)
        body = result["Body"].read()
        content_type = result.get("ContentType", "image/jpeg")
        return Response(content=body, media_type=content_type)
    except Exception as exc:
        raise HTTPException(404, f"Could not load object: {exc}") from exc

@app.get("/api/map/config")
def map_configuration() -> dict[str, object]:
    return map_config(active_registration.manifest_path, active_registration.gis_path)


@app.get("/api/operations")
def operational_view() -> dict[str, object]:
    materialization = materialized_state.refresh()
    scenario_id = operational_dashboard.scenario["scenario_id"]
    ai_events = materialized_state.events(
        DASHBOARD_TOPICS["ai"], scenario_id=scenario_id, replay_only=True
    )
    telemetry_events = materialized_state.events(
        DASHBOARD_TOPICS["telemetry"], scenario_id=scenario_id, replay_only=True
    )
    ai_events = _scenario_rows_visible_at_current_time(ai_events)
    telemetry_events = _scenario_rows_visible_at_current_time(telemetry_events)
    result = operational_dashboard.snapshot(ai_events, telemetry_events)
    result["source_errors"] = {
        "ai_analysis_results": materialization["sources"][DASHBOARD_TOPICS["ai"]]["error"],
        "infrastructure_telemetry": materialization["sources"][DASHBOARD_TOPICS["telemetry"]]["error"],
    }
    result.setdefault("diagnostics", {})["materialization"] = materialization
    return result


@app.get("/api/operations/snapshot")
def operational_snapshot() -> dict[str, object]:
    return dashboard_projection.snapshot()


@app.get("/api/scenario")
def scenario_status() -> dict[str, object]:
    return {**scenario_runner.status(), "scenario_id": active_registration.scenario_id, "title": active_registration.title}


@app.get("/api/scenarios")
def registered_scenarios() -> dict[str, object]:
    rows = []
    for registration in scenario_registry.list():
        row = registration.public()
        if registration.enabled:
            readiness, _, cache = readiness_cache.get(
                f"scenario:{registration.scenario_id}",
                lambda registration=registration: (DashboardScenarioService(registration.manifest_path).readiness(), None),
            )
            row["readiness"] = readiness
            row["readiness_cache"] = cache
        row["active"] = registration.scenario_id == active_registration.scenario_id
        rows.append(row)
    return {"active_scenario_id": active_registration.scenario_id, "default_scenario_id": scenario_registry.default_scenario_id, "scenarios": rows}


@app.post("/api/scenarios/{scenario_id}/activate", dependencies=[Depends(require_operator)])
def activate_scenario(scenario_id: str) -> dict[str, object]:
    current = scenario_runner.status()
    if current.get("state") in {"running", "paused"}:
        raise HTTPException(409, "Pause is not sufficient; reset the active replay before switching scenarios.")
    observation_status = manager.status("observation")
    observation_state = observation_status.get("state")
    observation_was_managed = (
        observation_state == "running"
        and observation_status.get("ownership") == "dashboard"
    )
    if observation_state == "externally_running":
        raise HTTPException(
            409,
            "The observation worker is externally managed. Stop it before switching scenarios.",
        )
    previous_registration = active_registration
    if observation_was_managed:
        stopped, message = manager.stop("observation")
        if not stopped:
            logger.error("Could not stop observation worker for scenario switch: %s", message)
            raise HTTPException(409, "The observation worker could not be restarted for the scenario switch.")
    try:
        registration = scenario_registry.get(scenario_id)
        readiness = DashboardScenarioService(registration.manifest_path).readiness()
        if not readiness.get("valid"):
            raise HTTPException(409, readiness.get("message", "Scenario is not ready."))
        _activate_scenario_context(scenario_id)
        if observation_was_managed:
            started, message = manager.start("observation", _observation_command(registration))
            if not started:
                logger.error("Could not restart observation worker after scenario switch: %s", message)
                _activate_scenario_context(previous_registration.scenario_id)
                manager.start("observation", _observation_command(previous_registration))
                raise HTTPException(500, "Scenario switching failed while restarting its observation worker.")
    except HTTPException:
        if observation_was_managed and manager.status("observation").get("state") != "running":
            manager.start("observation", _observation_command(previous_registration))
        raise
    except KeyError as exc:
        if observation_was_managed:
            manager.start("observation", _observation_command(previous_registration))
        raise HTTPException(404, "Unknown registered scenario") from exc
    except ValueError as exc:
        if observation_was_managed:
            manager.start("observation", _observation_command(previous_registration))
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "message": f"Activated {active_registration.title}", "scenario": active_registration.public(), "status": scenario_runner.status()}


@app.post("/api/scenario/{action}", dependencies=[Depends(require_operator)])
def scenario_control(
    action: Literal["start", "pause", "resume", "reset", "speed"],
    options: ScenarioControlRequest,
) -> dict[str, object]:
    try:
        status = scenario_runner.control(action, options.speed)
        if action == "reset":
            materialized_state.refresh(force=True)
            materialized_state.reset_replay(
                operational_dashboard.scenario["scenario_id"]
            )
            scenario_social_alerts.reset()
    except RuntimeError as exc:
        logger.exception("Scenario action %s rejected in the current state", action)
        raise HTTPException(409, "Scenario action is not valid in the current state.") from exc
    except (OSError, ValueError, KeyError) as exc:
        logger.exception("Scenario action %s failed", action)
        raise HTTPException(422, "Scenario action failed validation. Check local dashboard logs.") from exc
    dashboard_projection.invalidate()
    return {"ok": True, "message": f"Scenario {action} accepted", "status": status}

app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="dashboard")
