"""Manage local pipeline producers and processing workers for the web dashboard."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs" / "dashboard"
STATE_PATH = PROJECT_ROOT / ".runtime" / "dashboard-processes.json"
PROCESS_SIGNATURES = {
    "spark": "spark_streaming.py",
    "ai": "ai.computer_vision.worker",
    "satellite_change": "core.satellite.change_worker",
    "observation": "consumers.observation_consumer",
    "social": "data_source.social.social_producer",
    "drone": "data_source.drone.drone_producer",
    "satellite": "data_source.satellite.satellite_producer",
    "gis": "data_source.gis.gis_producer",
}


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    process: subprocess.Popen[str]
    log_path: Path
    log_handle: object


ProcessInspector = Callable[[], dict[int, str]]


def discover_processes() -> dict[int, str]:
    """Return PID-to-command mappings used to identify externally started workers."""
    if os.name == "nt":
        command = [
            "powershell", "-NoProfile", "-Command",
            "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress",
        ]
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=8, check=False
        )
        if completed.returncode != 0:
            raise OSError(completed.stderr.strip() or "Windows process inspection failed")
        payload = json.loads(completed.stdout or "[]")
        rows = payload if isinstance(payload, list) else [payload]
        return {
            int(row["ProcessId"]): str(row.get("CommandLine") or "")
            for row in rows if row.get("ProcessId") is not None
        }

    completed = subprocess.run(
        ["ps", "-eo", "pid=,args="], capture_output=True, text=True,
        timeout=8, check=False,
    )
    if completed.returncode != 0:
        raise OSError(completed.stderr.strip() or "Process inspection failed")
    processes: dict[int, str] = {}
    for line in completed.stdout.splitlines():
        fields = line.strip().split(maxsplit=1)
        if fields:
            processes[int(fields[0])] = fields[1] if len(fields) > 1 else ""
    return processes


class ProcessManager:
    """Thread-safe process registry with restart-safe state and external discovery."""

    def __init__(
        self,
        state_path: Path | None = None,
        inspector: ProcessInspector | None = None,
    ) -> None:
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()
        self._state_path = state_path or STATE_PATH
        self._inspector = inspector or discover_processes
        self._history = self._load_history()
        self._inspection_cache: tuple[float, dict[int, str] | None, str | None] = (
            0.0, None, None
        )
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def start(self, name: str, command: list[str]) -> tuple[bool, str]:
        with self._lock:
            current_status = self.status(name)
            if current_status["state"] in {"running", "externally_running"}:
                return False, f"{name} is already running."
            if current_status["state"] == "unknown":
                return False, f"Could not verify whether {name} is already running."

            log_path = LOG_DIR / f"{name}.log"
            log_handle = log_path.open("a", encoding="utf-8", buffering=1)
            log_handle.write(f"\n--- starting: {' '.join(command)} ---\n")
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
            try:
                environment = self._environment(name)
                process = subprocess.Popen(
                    command, cwd=PROJECT_ROOT, stdout=log_handle,
                    stderr=subprocess.STDOUT, text=True,
                    creationflags=creationflags, env=environment,
                )
            except OSError as exc:
                log_handle.close()
                message = f"Could not start {name}: {exc}"
                self._history[name] = self._record(
                    name, command, state="exited", last_failure=message
                )
                self._persist()
                return False, message

            self._processes[name] = ManagedProcess(
                name, command, process, log_path, log_handle
            )
            self._history[name] = self._record(
                name, command, state="running", pid=process.pid
            )
            self._persist()
            return True, f"Started {name} (PID {process.pid})."

    def stop(self, name: str) -> tuple[bool, str]:
        with self._lock:
            managed = self._processes.get(name)
            if not managed or managed.process.poll() is not None:
                self._close_handle(managed)
                if self.status(name)["state"] == "externally_running":
                    return False, f"{name} is externally running; stop it where it was started."
                return False, f"{name} is not running."
            try:
                managed.process.send_signal(
                    signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGINT
                )
                managed.process.wait(timeout=8)
            except (OSError, subprocess.TimeoutExpired):
                managed.process.terminate()
                try:
                    managed.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    managed.process.kill()
                    managed.process.wait(timeout=5)
            finally:
                self._close_handle(managed)
            record = self._history.get(name, self._record(name, managed.command))
            record.update(
                state="stopped", running=False,
                exit_code=managed.process.poll(), stopped_at=self._now(),
                last_failure=None,
            )
            self._history[name] = record
            self._persist()
            return True, f"Stopped {name}."

    def status(self, name: str) -> dict[str, object]:
        with self._lock:
            managed = self._processes.get(name)
            if managed:
                exit_code = managed.process.poll()
                if exit_code is None:
                    return self._public_state(self._history[name], state="running")
                self._close_handle(managed)
                record = self._history[name]
                if record.get("state") != "stopped":
                    record.update(
                        state="exited", running=False, exit_code=exit_code,
                        stopped_at=self._now(),
                        last_failure=(
                            f"Process exited with code {exit_code}." if exit_code else None
                        ),
                    )
                    self._persist()
                return self._public_state(record)

            processes, inspection_error = self._process_snapshot()
            record = self._history.get(name)
            if processes is not None:
                pid = self._matching_pid(name, processes, record)
                if pid is not None:
                    external = dict(record or self._record(name, [], pid=pid))
                    external.update(
                        state="externally_running", running=True, pid=pid,
                        ownership="recovered" if record else "external",
                    )
                    return self._public_state(external)
                if record and record.get("state") in {"running", "externally_running"}:
                    record.update(
                        state="exited", running=False, stopped_at=self._now()
                    )
                    self._persist()

            if record:
                return self._public_state(record)
            if inspection_error:
                return {
                    "state": "unknown", "running": None, "pid": None,
                    "exit_code": None, "last_failure": inspection_error,
                    "ownership": "unknown",
                }
            return {
                "state": "stopped", "running": False, "pid": None,
                "exit_code": None, "last_failure": None, "ownership": "none",
            }

    def log_tail(self, name: str, lines: int = 120) -> str:
        with self._lock:
            managed = self._processes.get(name)
            log_path = managed.log_path if managed else LOG_DIR / f"{name}.log"
        if not log_path.exists():
            return "No logs yet."
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return f"Could not read log: {exc}"
        return "\n".join(content[-lines:]) or "Log is empty."

    def _environment(self, name: str) -> dict[str, str]:
        environment = os.environ.copy()
        scripts_dir = str(Path(sys.executable).parent)
        environment["PATH"] = scripts_dir + os.pathsep + environment.get("PATH", "")
        environment["PYSPARK_PYTHON"] = sys.executable
        environment["PYSPARK_DRIVER_PYTHON"] = sys.executable
        if name == "spark":
            java_home = find_java17_home()
            if java_home:
                environment["JAVA_HOME"] = str(java_home)
                environment["PATH"] = str(java_home / "bin") + os.pathsep + environment["PATH"]
            hadoop_home = PROJECT_ROOT / ".runtime" / "hadoop-3.3.5"
            if (hadoop_home / "bin" / "winutils.exe").is_file():
                environment["HADOOP_HOME"] = str(hadoop_home)
                environment["PATH"] = str(hadoop_home / "bin") + os.pathsep + environment["PATH"]
        return environment

    def _process_snapshot(self) -> tuple[dict[int, str] | None, str | None]:
        cached_at, processes, error = self._inspection_cache
        if time.monotonic() - cached_at < 2:
            return processes, error
        try:
            processes, error = self._inspector(), None
        except (OSError, subprocess.SubprocessError, ValueError, json.JSONDecodeError) as exc:
            processes, error = None, f"Process state could not be inspected: {exc}"
        self._inspection_cache = (time.monotonic(), processes, error)
        return processes, error

    @staticmethod
    def _matching_pid(
        name: str, processes: dict[int, str], record: dict[str, object] | None
    ) -> int | None:
        signature = PROCESS_SIGNATURES.get(name)
        recorded_pid = record.get("pid") if record else None
        if isinstance(recorded_pid, int) and recorded_pid in processes:
            if not signature or signature.lower() in processes[recorded_pid].lower():
                return recorded_pid
        if signature:
            for pid, command_line in processes.items():
                if signature.lower() in command_line.lower():
                    return pid
        return None

    def _record(
        self, name: str, command: list[str], *, state: str = "stopped",
        pid: int | None = None, exit_code: int | None = None,
        last_failure: str | None = None,
    ) -> dict[str, object]:
        return {
            "name": name, "command": command, "state": state,
            "running": state in {"running", "externally_running"}, "pid": pid,
            "exit_code": exit_code, "last_failure": last_failure,
            "started_at": self._now() if state == "running" else None,
            "stopped_at": None, "ownership": "dashboard",
        }

    @staticmethod
    def _public_state(record: dict[str, object], **overrides: object) -> dict[str, object]:
        value = dict(record)
        value.update(overrides)
        value["running"] = value.get("state") in {"running", "externally_running"}
        value.pop("command", None)
        value.pop("name", None)
        return value

    def _load_history(self) -> dict[str, dict[str, object]]:
        try:
            payload = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _persist(self) -> None:
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(self._history, indent=2, sort_keys=True), encoding="utf-8"
        )
        temporary.replace(self._state_path)

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _close_handle(managed: ManagedProcess | None) -> None:
        if managed and not getattr(managed.log_handle, "closed", True):
            managed.log_handle.close()


def python_module(module: str, *arguments: str) -> list[str]:
    return [sys.executable, "-m", module, *arguments]


def spark_command() -> list[str]:
    scripts_dir = Path(sys.executable).parent
    executable = scripts_dir / ("spark-submit.cmd" if os.name == "nt" else "spark-submit")
    return [
        str(executable), "--packages",
        "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.5",
        str(PROJECT_ROOT / "spark" / "spark_streaming.py"),
    ]


def find_java17_home() -> Path | None:
    """Prefer an explicit or standard Windows Java 17 installation for Spark 3.5."""
    configured = os.getenv("JAVA17_HOME")
    candidates: list[Path] = [Path(configured)] if configured else []
    if os.name == "nt":
        candidates.extend(Path("C:/Program Files/Eclipse Adoptium").glob("jdk-17*"))
        candidates.extend(Path("C:/Program Files/Java").glob("jdk-17*"))
    for candidate in sorted(candidates, reverse=True):
        if (candidate / "bin" / ("java.exe" if os.name == "nt" else "java")).is_file():
            return candidate
    return None
