"""FastAPI backend for the local disaster streaming control center."""

from __future__ import annotations

from dashboard.map_api import map_config
from dashboard.social_alert_service import SocialAlertService

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


class StartOptions(BaseModel):
    limit: int = Field(default=20, ge=1, le=10000)
    delay: float | None = Field(default=None, ge=0, le=60)


class ResponderDecision(BaseModel):
    responder_id: str = Field(min_length=1, max_length=200)


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
    latest = rows[0] if rows else None
    if latest:
        for image in (latest.get("source_images") or {}).values():
            key = image.get("object_key") if isinstance(image, dict) else None
            if isinstance(key, str) and key.startswith("satellite/"):
                image["preview_url"] = "/api/object?key=" + key
    return {"latest": latest, "result_count": len(rows), "error": error}


@app.get("/api/social/alerts")
def social_alert_state() -> dict[str, object]:
    rows, error = recent_topic_events("social-posts", 100)
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

app.mount("/", StaticFiles(directory=STATIC_ROOT, html=True), name="dashboard")
