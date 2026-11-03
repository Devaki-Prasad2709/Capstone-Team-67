"""Truthful dashboard readiness aggregation with actionable component states."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any


VALID_STATES = {"healthy", "degraded", "stopped", "unavailable", "unknown"}
REQUIRED_INFRASTRUCTURE = ("kafka", "minio", "zookeeper")
REQUIRED_WORKERS = ("spark", "ai", "satellite_change", "observation")
OPTIONAL_PRODUCERS = ("social", "drone", "satellite", "gis")


def component(
    state: str,
    message: str,
    *,
    required: bool = True,
    action: str | None = None,
    **details: Any,
) -> dict[str, Any]:
    if state not in VALID_STATES:
        raise ValueError(f"Unsupported readiness state: {state}")
    return {
        "state": state,
        "required": required,
        "message": message,
        "action": action,
        **details,
    }


def aggregate(components: dict[str, dict[str, Any]]) -> str:
    states = [item["state"] for item in components.values() if item.get("required", True)]
    for state in ("unavailable", "unknown", "degraded", "stopped"):
        if state in states:
            return state
    return "healthy"


def infrastructure_readiness(
    docker_rows: list[dict[str, Any]],
    docker_error: str | None,
    kafka_error: str | None,
    minio_error: str | None,
) -> dict[str, Any]:
    rows = {
        str(row.get("Service") or row.get("Name") or "").lower(): row
        for row in docker_rows
    }
    components: dict[str, dict[str, Any]] = {}
    for name in REQUIRED_INFRASTRUCTURE:
        row = rows.get(name)
        if docker_error:
            value = component(
                "unavailable",
                "Docker state could not be inspected.",
                action="Start Docker Desktop and retry the health check.",
            )
        elif row is None:
            value = component(
                "stopped",
                f"{name.title()} container is not running.",
                action="Start infrastructure from System Control.",
            )
        else:
            runtime = str(row.get("State") or "unknown").lower()
            health = str(row.get("Health") or "").lower()
            if runtime not in {"running", "up"}:
                value = component(
                    "stopped" if runtime in {"exited", "stopped", "created"} else "unknown",
                    f"{name.title()} container state is {runtime or 'unknown'}.",
                    action="Start or inspect the container in System Control.",
                    runtime_state=runtime,
                    container_health=health or None,
                )
            elif health == "unhealthy":
                value = component(
                    "unavailable",
                    f"{name.title()} container is running but its healthcheck is failing.",
                    action=f"Inspect docker compose logs {name}.",
                    runtime_state=runtime,
                    container_health=health,
                )
            elif health in {"starting", ""}:
                value = component(
                    "degraded",
                    f"{name.title()} is running but is not yet confirmed healthy.",
                    action="Wait for the container healthcheck or inspect its logs.",
                    runtime_state=runtime,
                    container_health=health or "not-reported",
                )
            else:
                value = component(
                    "healthy",
                    f"{name.title()} container is healthy.",
                    runtime_state=runtime,
                    container_health=health,
                )
        components[name] = value

    if kafka_error and components["kafka"]["state"] == "healthy":
        components["kafka"] = component(
            "unavailable",
            "Kafka broker is unreachable.",
            action="Check port 9092, advertised listeners and Kafka logs.",
        )
    if minio_error and components["minio"]["state"] == "healthy":
        components["minio"] = component(
            "unavailable",
            "MinIO API is unreachable.",
            action="Check port 9000, credentials and MinIO logs.",
        )
    return {"state": aggregate(components), "components": components}


def process_component(name: str, value: dict[str, Any], *, required: bool) -> dict[str, Any]:
    process_state = str(value.get("state") or "unknown")
    label = name.replace("_", " ").title()
    if process_state in {"running", "externally_running"}:
        return component(
            "healthy",
            f"{label} is running.",
            required=required,
            process_state=process_state,
            pid=value.get("pid"),
        )
    if process_state == "stopped":
        return component(
            "stopped",
            f"{label} is stopped" + (" and required for scenario processing." if required else "; it is optional until a standalone replay is requested."),
            required=required,
            action=("Start this processing worker." if required else "Start this producer only for standalone dataset replay."),
            process_state=process_state,
        )
    if process_state == "exited":
        failure = value.get("last_failure") or f"exit code {value.get('exit_code')}"
        return component(
            "unavailable",
            f"{label} exited unexpectedly: {failure}",
            required=required,
            action="Inspect the worker log, correct the failure and restart it.",
            process_state=process_state,
            exit_code=value.get("exit_code"),
        )
    return component(
        "unknown",
        f"{label} state could not be verified: {value.get('last_failure') or process_state}.",
        required=required,
        action="Check host process permissions and refresh status.",
        process_state=process_state,
    )


def processing_readiness(processes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    components = {
        name: process_component(name, processes.get(name, {}), required=True)
        for name in REQUIRED_WORKERS
    }
    return {"state": aggregate(components), "components": components}


def source_readiness(
    paths: dict[str, tuple[str, bool]], processes: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}
    for name, (raw_path, required) in paths.items():
        path = Path(raw_path).expanduser() if raw_path else None
        if not raw_path:
            components[name] = component(
                "unavailable" if required else "stopped",
                f"{name} path is not configured" + ("." if required else "; this optional source is disabled."),
                required=required,
                action=f"Set the corresponding {name.upper()} environment variable.",
            )
        elif not path.exists():
            components[name] = component(
                "unavailable",
                f"Configured {name} path is unavailable.",
                required=required,
                action="Reconnect the dataset drive or correct the configured path.",
            )
        else:
            components[name] = component(
                "healthy", f"{name} source is accessible.", required=required
            )
    for name in OPTIONAL_PRODUCERS:
        components[f"{name}_producer"] = process_component(
            name, processes.get(name, {}), required=False
        )
    return {"state": aggregate(components), "components": components}


def freshness_readiness(
    last_success: dict[str, str | None], current_errors: dict[str, str | None]
) -> dict[str, Any]:
    components: dict[str, dict[str, Any]] = {}
    now = datetime.now(timezone.utc)
    for name in ("kafka", "minio", "tgnn"):
        timestamp = last_success.get(name)
        error = current_errors.get(name)
        if error and timestamp:
            value = component(
                "degraded",
                f"Latest {name.upper()} refresh failed; retaining the last successful update.",
                required=False,
                action=f"Resolve the {name} error and refresh.",
                last_success=timestamp,
                current_error=True,
            )
        elif error:
            value = component(
                "unavailable",
                f"No successful {name.upper()} update is available.",
                required=False,
                action=f"Resolve the {name} connection or processing error.",
                last_success=None,
                current_error=True,
            )
        elif not timestamp:
            value = component(
                "unknown",
                f"No successful {name.upper()} update has been recorded yet.",
                required=False,
                action="Run or refresh the relevant pipeline stage.",
                last_success=None,
            )
        else:
            try:
                parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                age = max(0.0, (now - parsed).total_seconds())
            except (TypeError, ValueError):
                age = None
            stale = age is not None and age > 60
            value = component(
                "degraded" if stale else "healthy",
                f"Last successful {name.upper()} update was {round(age)} seconds ago." if age is not None else f"Last successful {name.upper()} update time is malformed.",
                required=False,
                action="Refresh the active view and inspect the corresponding service." if stale else None,
                last_success=timestamp,
                age_seconds=age,
            )
        components[name] = value
    return {"state": aggregate({k: {**v, "required": True} for k, v in components.items()}), "components": components}


def overall_readiness(groups: dict[str, dict[str, Any]]) -> dict[str, Any]:
    blocking = {name: value for name, value in groups.items() if name != "freshness"}
    state = aggregate({name: {"state": value["state"], "required": True} for name, value in blocking.items()})
    can_run = all(value["state"] == "healthy" for value in blocking.values())
    if can_run:
        message = "The complete scenario is ready to run."
    else:
        blockers = [name for name, value in blocking.items() if value["state"] != "healthy"]
        message = "Scenario is not ready: " + ", ".join(blockers) + " require attention."
    return {"state": state, "can_run_scenario": can_run, "message": message}
