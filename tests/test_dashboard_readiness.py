from dashboard.readiness import (
    freshness_readiness,
    infrastructure_readiness,
    overall_readiness,
    processing_readiness,
    source_readiness,
)


def healthy_containers():
    return [
        {"Service": name, "State": "running", "Health": "healthy"}
        for name in ("kafka", "minio", "zookeeper")
    ]


def running_processes():
    return {
        name: {"state": "running", "pid": index + 100}
        for index, name in enumerate(
            ("spark", "ai", "satellite_change", "observation")
        )
    }


def test_unhealthy_docker_container_is_not_reported_healthy():
    containers = healthy_containers()
    containers[0]["Health"] = "unhealthy"

    result = infrastructure_readiness(containers, None, None, None)

    assert result["state"] == "unavailable"
    assert result["components"]["kafka"]["state"] == "unavailable"
    assert "healthcheck is failing" in result["components"]["kafka"]["message"]
    assert "logs kafka" in result["components"]["kafka"]["action"]


def test_stopped_required_worker_blocks_scenario_but_is_not_a_failure():
    processes = running_processes()
    processes["spark"] = {
        "state": "stopped",
        "exit_code": None,
        "last_failure": None,
    }

    result = processing_readiness(processes)

    assert result["state"] == "stopped"
    assert result["components"]["spark"]["state"] == "stopped"
    assert "required" in result["components"]["spark"]["message"]
    assert "unexpectedly" not in result["components"]["spark"]["message"]


def test_unreachable_kafka_overrides_healthy_container_state():
    result = infrastructure_readiness(
        healthy_containers(), None, "NoBrokersAvailable", None
    )

    assert result["state"] == "unavailable"
    kafka = result["components"]["kafka"]
    assert kafka["state"] == "unavailable"
    assert kafka["message"] == "Kafka broker is unreachable."
    assert "port 9092" in kafka["action"]


def test_stopped_optional_producer_does_not_block_ready_sources(tmp_path):
    spacenet = tmp_path / "spacenet"
    isbda = tmp_path / "isbda"
    model = tmp_path / "best.pt"
    spacenet.mkdir()
    isbda.mkdir()
    model.write_bytes(b"checkpoint")
    processes = {name: {"state": "stopped"} for name in ("social", "drone", "satellite", "gis")}

    result = source_readiness(
        {
            "spacenet8_dataset": (str(spacenet), True),
            "isbda_dataset": (str(isbda), True),
            "ai_model": (str(model), True),
        },
        processes,
    )

    assert result["state"] == "healthy"
    assert result["components"]["drone_producer"]["state"] == "stopped"
    assert result["components"]["drone_producer"]["required"] is False


def test_overall_readiness_answers_whether_complete_scenario_can_run():
    groups = {
        "infrastructure": {"state": "healthy"},
        "processing": {"state": "stopped"},
        "sources": {"state": "healthy"},
        "scenario": {"state": "healthy"},
        "freshness": {"state": "unknown"},
    }

    result = overall_readiness(groups)

    assert result["state"] == "stopped"
    assert result["can_run_scenario"] is False
    assert "processing" in result["message"]


def test_failed_refresh_preserves_last_success_as_degraded():
    result = freshness_readiness(
        {"kafka": "2026-11-02T12:00:00Z", "minio": None, "tgnn": None},
        {"kafka": "broker disconnected", "minio": None, "tgnn": None},
    )

    kafka = result["components"]["kafka"]
    assert kafka["state"] == "degraded"
    assert kafka["last_success"] == "2026-11-02T12:00:00Z"
    assert kafka["current_error"] is True
