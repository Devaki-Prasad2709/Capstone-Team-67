"""FastAPI backend for the local disaster streaming control center."""

from __future__ import annotations

from dashboard.map_api import map_config
from dashboard.social_alert_service import SocialAlertService
from dashboard.building_classification_service import BuildingClassificationService
from dashboard.operational_service import OperationalDashboardService
from dashboard.scenario_service import DashboardScenarioService

from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config.settings import settings
from dashboard.process_manager import ProcessManager, python_module, spark_command
from dashboard.services import (
    dedup_summary,
    ai_model_summary,
    docker_services,
    kafka_counts,
    minio_objects,
    object_client,
    parquet_preview,
    recent_topic_events,
    run_command,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = Path(__file__).resolve().parent / "static"
PROCESS_NAMES = (
    "spark", "ai", "satellite_change", "social", "drone", "satellite", "gis"
)

app = FastAPI(title="Disaster Streaming Control Center", docs_url="/api/docs")
manager = ProcessManager()
social_alerts = SocialAlertService()
building_classifications = BuildingClassificationService()
operational_dashboard = OperationalDashboardService(building_classifications)
scenario_runner = DashboardScenarioService()


class StartOptions(BaseModel):
    limit: int = Field(default=20, ge=1, le=10000)
    delay: float | None = Field(default=None, ge=0, le=60)


class ResponderDecision(BaseModel):
    responder_id: str = Field(min_length=1, max_length=200)


class BuildingClassificationRequest(BaseModel):
    assigned_type: str = Field(min_length=1, max_length=64)
    operator: str = Field(min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=2000)


class ScenarioControlRequest(BaseModel):
    speed: float | None = Field(default=None, gt=0, le=100)


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


@app.get("/api/status")
def status() -> dict[str, object]:
    docker, docker_error = docker_services()
    kafka, kafka_error = kafka_counts()
    minio, _, minio_error = minio_objects(1)
    dedup, dedup_error = dedup_summary()
    return {
        "configuration": {
            "kafka": settings.kafka_bootstrap_servers,
            "minio": settings.object_storage_endpoint_url,
            "transfer_mode": settings.image_transfer_mode,
            "spark_offsets": settings.spark_starting_offsets,
            "ai_model": str(settings.resolved_ai_model_path),
        },
        "docker": docker,
        "kafka": kafka,
        "minio": minio,
        "dedup": dedup,
        "processes": process_states(),
        "errors": {
            "docker": docker_error,
            "kafka": kafka_error,
            "minio": minio_error,
            "dedup": dedup_error,
        },
    }


@app.post("/api/infrastructure/{action}")
def infrastructure(action: Literal["start", "stop"]) -> dict[str, object]:
    command = (
        ["docker", "compose", "--env-file", ".env", "up", "-d"]
        if action == "start"
        else ["docker", "compose", "stop"]
    )
    ok, message = run_command(command, 120)
    return {"ok": ok, "message": message}


@app.post("/api/process/{name}/start")
def start_process(name: str, options: StartOptions) -> dict[str, object]:
    if name not in PROCESS_NAMES:
        raise HTTPException(404, "Unknown process")
    if name == "spark":
        command = spark_command()
    elif name == "ai":
        command = python_module("ai.computer_vision.worker", "--limit", str(options.limit))
    elif name == "satellite_change":
        command = python_module(
            "core.satellite.change_worker", "--limit", str(options.limit)
        )
    else:
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
    ok, message = manager.start(name, command)
    return {"ok": ok, "message": message, "status": manager.status(name)}


@app.post("/api/process/{name}/stop")
def stop_process(name: str) -> dict[str, object]:
    if name not in PROCESS_NAMES:
        raise HTTPException(404, "Unknown process")
    ok, message = manager.stop(name)
    return {"ok": ok, "message": message, "status": manager.status(name)}


@app.get("/api/logs/{name}")
def logs(name: str, lines: int = Query(default=150, ge=20, le=1000)) -> dict[str, str]:
    if name not in PROCESS_NAMES:
        raise HTTPException(404, "Unknown process")
    return {"name": name, "log": manager.log_tail(name, lines)}


@app.get("/api/spark/{source}")
def spark_output(source: Literal["social", "drone", "satellite", "gis", "ai"]) -> dict[str, object]:
    rows, files, error = parquet_preview(source)
    return {"source": source, "rows": rows, "files": files, "error": error}


@app.get("/api/ai")
def ai_output() -> dict[str, object]:
    model, model_error = ai_model_summary()
    rows, results_error = recent_topic_events("ai-analysis-results", 100)
    return {
        "model": model,
        "results": rows,
        "errors": {"model": model_error, "results": results_error},
    }


@app.get("/api/satellite/change")
def satellite_change_output() -> dict[str, object]:
    rows, error = recent_topic_events("satellite-change-results", 10)
    scenario_id = operational_dashboard.scenario["scenario_id"]
    rows = [row for row in rows if row.get("scenario_id") == scenario_id]
    rows = _scenario_rows_visible_at_current_time(rows)
    latest = rows[0] if rows else None
    if latest:
        for image in (latest.get("source_images") or {}).values():
            key = image.get("object_key") if isinstance(image, dict) else None
            if isinstance(key, str) and key.startswith("satellite/"):
                image["preview_url"] = "/api/object?key=" + key
    return {"latest": latest, "result_count": len(rows), "error": error}


@app.get("/api/social/alerts")
def social_alert_state(scenario_only: bool = Query(False)) -> dict[str, object]:
    rows, error = recent_topic_events("social-posts", 100)
    if scenario_only:
        scenario_id = operational_dashboard.scenario["scenario_id"]
        rows = [row for row in rows if row.get("scenario_id") == scenario_id]
        rows = _scenario_rows_visible_at_current_time(rows)
    social_alerts.ingest_events(rows)
    state = social_alerts.state()
    state["kafka_error"] = error
    return state


@app.post("/api/social/alerts/{alert_id}/{action}")
def review_social_alert(
    alert_id: str,
    action: Literal["confirm", "reject", "report_false"],
    decision: ResponderDecision,
) -> dict[str, object]:
    try:
        alert = social_alerts.decide(alert_id, action, decision.responder_id)
    except KeyError as exc:
        raise HTTPException(404, "Unknown social alert") from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {
        "ok": True,
        "message": f"Alert {alert_id} changed to {alert['status']}",
        "alert": alert,
        "state": social_alerts.state(),
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


@app.post("/api/buildings/{building_source_id}/classification")
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
    return {
        "ok": True,
        "message": f"Classification revision saved for {building_source_id}",
        **result,
    }


@app.get("/api/objects")
def objects() -> dict[str, object]:
    summary, rows, error = minio_objects()
    return {"summary": summary, "objects": rows, "error": error}


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
    return map_config()


@app.get("/api/operations")
def operational_view() -> dict[str, object]:
    ai_events, ai_error = recent_topic_events("ai-analysis-results", 200)
    telemetry_events, telemetry_error = recent_topic_events(
        "infrastructure-telemetry", 100
    )
    ai_events = _scenario_rows_visible_at_current_time(ai_events)
    telemetry_events = _scenario_rows_visible_at_current_time(telemetry_events)
    result = operational_dashboard.snapshot(ai_events, telemetry_events)
    result["source_errors"] = {
        "ai_analysis_results": ai_error,
        "infrastructure_telemetry": telemetry_error,
    }
    return result


@app.get("/api/scenario")
def scenario_status() -> dict[str, object]:
    return scenario_runner.status()


@app.post("/api/scenario/{action}")
def scenario_control(
    action: Literal["start", "pause", "resume", "reset", "speed"],
    options: ScenarioControlRequest,
) -> dict[str, object]:
    try:
        status = scenario_runner.control(action, options.speed)
        if action == "reset":
            social_alerts.reset()
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True, "message": f"Scenario {action} accepted", "status": status}

app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="dashboard")
