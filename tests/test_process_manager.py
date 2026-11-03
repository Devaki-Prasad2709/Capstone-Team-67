from __future__ import annotations

import json
from pathlib import Path

from dashboard import process_manager


class FakeProcess:
    next_pid = 4100

    def __init__(self, command, **kwargs):
        self.command = command
        self.pid = FakeProcess.next_pid
        FakeProcess.next_pid += 1
        self.exit_code = None
        self.signals = []

    def poll(self):
        return self.exit_code

    def send_signal(self, value):
        self.signals.append(value)
        self.exit_code = 0

    def wait(self, timeout=None):
        if self.exit_code is None:
            self.exit_code = 0
        return self.exit_code

    def terminate(self):
        self.exit_code = -15

    def kill(self):
        self.exit_code = -9


def manager(tmp_path: Path, inspector=lambda: {}):
    return process_manager.ProcessManager(tmp_path / "processes.json", inspector)


def test_unseen_process_is_stopped_and_inspection_failure_is_unknown(tmp_path):
    assert manager(tmp_path).status("ai")["state"] == "stopped"

    def unavailable():
        raise OSError("inspection unavailable")

    state = manager(tmp_path / "other", unavailable).status("ai")
    assert state["state"] == "unknown"
    assert state["running"] is None
    assert "inspection unavailable" in state["last_failure"]


def test_external_process_is_reported_and_cannot_be_stopped(tmp_path):
    external = manager(
        tmp_path, lambda: {8123: "python -m ai.computer_vision.worker"}
    )
    state = external.status("ai")
    assert state["state"] == "externally_running"
    assert state["ownership"] == "external"
    assert state["pid"] == 8123
    ok, message = external.stop("ai")
    assert ok is False
    assert "externally running" in message


def test_running_exit_and_failure_are_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(process_manager.subprocess, "Popen", FakeProcess)
    state_path = tmp_path / "processes.json"
    current = process_manager.ProcessManager(state_path, lambda: {})
    ok, _ = current.start("ai", ["python", "-m", "ai.computer_vision.worker"])
    assert ok is True
    assert current.status("ai")["state"] == "running"

    current._processes["ai"].process.exit_code = 7
    exited = current.status("ai")
    assert exited["state"] == "exited"
    assert exited["exit_code"] == 7
    assert exited["last_failure"] == "Process exited with code 7."

    persisted = json.loads(state_path.read_text(encoding="utf-8"))["ai"]
    assert persisted["exit_code"] == 7
    assert persisted["last_failure"] == "Process exited with code 7."


def test_dashboard_restart_recovers_live_pid_as_external(tmp_path, monkeypatch):
    monkeypatch.setattr(process_manager.subprocess, "Popen", FakeProcess)
    state_path = tmp_path / "processes.json"
    original = process_manager.ProcessManager(state_path, lambda: {})
    original.start("satellite_change", [
        "python", "-m", "core.satellite.change_worker"
    ])
    pid = original.status("satellite_change")["pid"]

    restarted = process_manager.ProcessManager(
        state_path,
        lambda: {pid: "python -m core.satellite.change_worker"},
    )
    recovered = restarted.status("satellite_change")
    assert recovered["state"] == "externally_running"
    assert recovered["ownership"] == "recovered"
    assert recovered["pid"] == pid


def test_intentional_stop_preserves_exit_code_without_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(process_manager.subprocess, "Popen", FakeProcess)
    current = manager(tmp_path)
    current.start("ai", ["python", "-m", "ai.computer_vision.worker"])
    ok, _ = current.stop("ai")
    state = current.status("ai")
    assert ok is True
    assert state["state"] == "stopped"
    assert state["exit_code"] == 0
    assert state["last_failure"] is None


def test_start_failure_is_visible(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("binary missing")

    monkeypatch.setattr(process_manager.subprocess, "Popen", fail)
    current = manager(tmp_path)
    ok, message = current.start("ai", ["missing"])
    state = current.status("ai")
    assert ok is False
    assert "binary missing" in message
    assert state["state"] == "exited"
    assert "binary missing" in state["last_failure"]


def test_pipeline_ui_separates_workers_and_producers_and_confirms_infra_stop():
    root = Path(__file__).parents[1]
    html = (root / "dashboard/static/index.html").read_text(encoding="utf-8")
    static = root / "dashboard/static"
    javascript = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [static / "app.js", *sorted((static / "modules").glob("*.js"))]
    )
    assert 'id="workerProcessGrid"' in html
    assert 'id="producerProcessGrid"' in html
    assert 'id="workerMaxEvents"' in html
    assert "window.confirm('Stop Kafka, MinIO" in javascript
    assert "externally_running" in javascript


def test_observation_worker_uses_the_frozen_scenario_geojson():
    source = (Path(__file__).parents[1] / "dashboard" / "server.py").read_text(
        encoding="utf-8"
    )
    assert "active_registration.gis_path" in source
    assert "disaster-observation-dashboard-" in source
    assert '"gis_snapshot.geojson"' not in source
