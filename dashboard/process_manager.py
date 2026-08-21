"""Manage long-running local pipeline processes for the Streamlit dashboard."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = PROJECT_ROOT / "logs" / "dashboard"


@dataclass
class ManagedProcess:
    name: str
    command: list[str]
    process: subprocess.Popen[str]
    log_path: Path
    log_handle: object


class ProcessManager:
    """Thread-safe registry for processes launched from the dashboard."""

    def __init__(self) -> None:
        self._processes: dict[str, ManagedProcess] = {}
        self._lock = threading.RLock()
        LOG_DIR.mkdir(parents=True, exist_ok=True)

    def start(self, name: str, command: list[str]) -> tuple[bool, str]:
        with self._lock:
            current = self._processes.get(name)
            if current and current.process.poll() is None:
                return False, f"{name} is already running."

            log_path = LOG_DIR / f"{name}.log"
            log_handle = log_path.open("w", encoding="utf-8", buffering=1)
            log_handle.write(f"\n--- starting: {' '.join(command)} ---\n")
            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
            try:
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
                process = subprocess.Popen(
                    command,
                    cwd=PROJECT_ROOT,
                    stdout=log_handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=creationflags,
                    env=environment,
                )
            except OSError as exc:
                log_handle.close()
                return False, f"Could not start {name}: {exc}"

            self._processes[name] = ManagedProcess(
                name=name,
                command=command,
                process=process,
                log_path=log_path,
                log_handle=log_handle,
            )
            return True, f"Started {name} (PID {process.pid})."

    def stop(self, name: str) -> tuple[bool, str]:
        with self._lock:
            managed = self._processes.get(name)
            if not managed or managed.process.poll() is not None:
                self._close_handle(managed)
                return False, f"{name} is not running."
            try:
                if os.name == "nt":
                    managed.process.send_signal(signal.CTRL_BREAK_EVENT)
                else:
                    managed.process.send_signal(signal.SIGINT)
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
            return True, f"Stopped {name}."

    def status(self, name: str) -> dict[str, object]:
        with self._lock:
            managed = self._processes.get(name)
            if not managed:
                return {"running": False, "pid": None, "exit_code": None}
            exit_code = managed.process.poll()
            if exit_code is not None:
                self._close_handle(managed)
            return {
                "running": exit_code is None,
                "pid": managed.process.pid,
                "exit_code": exit_code,
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
        str(executable),
        "--packages",
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
