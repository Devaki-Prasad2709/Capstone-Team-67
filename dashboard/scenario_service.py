"""Dashboard lifecycle wrapper for the production scenario controller."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock

from config.settings import settings
from scenario_runtime import ScenarioController, ScenarioEventFactory, ScenarioKafkaDispatcher
from scripts.validate_scenario import validate_scenario


class DashboardScenarioService:
    def __init__(self, scenario_path: Path | None = None):
        raw = scenario_path or Path(settings.final_scenario_path)
        self.scenario_path = raw if raw.is_absolute() else settings.project_root / raw
        self.timeline = json.loads(
            (self.scenario_path.parent / "timeline.json").read_text(encoding="utf-8")
        )
        self._controller = None
        self._dispatcher = None
        self._speed = float(self.timeline.get("speed", 1.0))
        self._lock = RLock()

    def _idle_status(self):
        return {
            "state": "idle",
            "speed": self._speed,
            "simulation_elapsed_seconds": 0.0,
            "simulation_timestamp": self.timeline["scenario_start"],
            "current_event": None,
            "completed_events": [],
            "upcoming_events": self.timeline["events"],
            "error": None,
        }

    def status(self):
        with self._lock:
            return self._controller.status() if self._controller else self._idle_status()

    def _create(self):
        spacenet = Path(settings.spacenet8_dataset_path).expanduser()
        isbda = Path(settings.isbda_dataset_path).expanduser()
        if not settings.spacenet8_dataset_path or not settings.isbda_dataset_path:
            raise ValueError("Configure SPACENET8_DATASET_PATH and ISBDA_DATASET_PATH")
        validation = validate_scenario(
            self.scenario_path,
            strict=True,
            spacenet_root=spacenet,
            isbda_root=isbda,
        )
        if not validation["valid"]:
            raise ValueError("Strict scenario validation failed")
        factory = ScenarioEventFactory(
            self.scenario_path, spacenet_root=spacenet, isbda_root=isbda
        )
        self._dispatcher = ScenarioKafkaDispatcher(factory)
        self._controller = ScenarioController(
            self.timeline["events"],
            self._dispatcher,
            self.timeline["scenario_start"],
            speed=self._speed,
        )

    def control(self, action: str, speed: float | None = None):
        with self._lock:
            if action == "speed":
                if speed is None:
                    raise ValueError("speed is required")
                self._speed = float(speed)
                if self._controller:
                    self._controller.set_speed(self._speed)
            elif action == "start":
                if self._controller is None:
                    self._create()
                self._controller.start()
            elif action == "pause":
                if self._controller is None:
                    raise RuntimeError("Cannot pause before start")
                self._controller.pause()
            elif action == "resume":
                if self._controller is None:
                    raise RuntimeError("Cannot resume before start")
                self._controller.resume()
            elif action == "reset":
                if self._controller:
                    self._controller.reset()
            else:
                raise ValueError(f"Unknown scenario action: {action}")
            return self.status()
