"""Dashboard lifecycle wrapper for the production scenario controller."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
import logging

from config.settings import settings
from scenario_runtime import ScenarioController, ScenarioEventFactory, ScenarioKafkaDispatcher
from scripts.validate_scenario import validate_scenario


logger = logging.getLogger(__name__)


class DashboardScenarioService:
    def __init__(self, scenario_path: Path | None = None):
        raw = scenario_path or Path(settings.final_scenario_path)
        self.scenario_path = raw if raw.is_absolute() else settings.project_root / raw
        self.scenario = json.loads(self.scenario_path.read_text(encoding="utf-8"))
        self.timeline = json.loads(
            (self.scenario_path.parent / "timeline.json").read_text(encoding="utf-8")
        )
        self._controller = None
        self._dispatcher = None
        self._speed = float(self.timeline.get("speed", 1.0))
        self._lock = RLock()

    def _dataset_roots(self) -> tuple[Path | None, Path | None]:
        pair = json.loads(
            (self.scenario_path.parent / "satellite" / "pair.json").read_text(encoding="utf-8")
        )
        satellite_env = pair.get("dataset_root_env", "SPACENET8_DATASET_PATH")
        satellite_value = {
            "SPACENET8_DATASET_PATH": settings.spacenet8_dataset_path,
            "XBD_DATASET_PATH": settings.xbd_dataset_path,
        }.get(satellite_env, "")
        return (
            Path(satellite_value).expanduser() if satellite_value else None,
            Path(settings.isbda_dataset_path).expanduser() if settings.isbda_dataset_path else None,
        )

    def _social_dataset_root(self) -> Path | None:
        if not (self.scenario_path.parent / "social" / "source.json").exists():
            return None
        return Path(settings.crisismmd_dataset_path).expanduser() if settings.crisismmd_dataset_path else None

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
            result = self._controller.status() if self._controller else self._idle_status()
            return {**result, "scenario_id": self.scenario["scenario_id"], "title": self.scenario["title"]}

    def readiness(self) -> dict:
        """Validate the frozen package without starting or mutating the replay."""
        satellite_root, drone_root = self._dataset_roots()
        social_required = (self.scenario_path.parent / "social" / "source.json").exists()
        social_root = self._social_dataset_root()
        if satellite_root is None or drone_root is None or (social_required and social_root is None):
            return {
                "state": "unavailable",
                "valid": False,
                "message": "Strict scenario validation cannot run until its registered external dataset paths are configured.",
                "action": "Configure the satellite, drone, and any registered social dataset roots, then refresh readiness.",
            }
        try:
            result = validate_scenario(
                self.scenario_path,
                strict=True,
                spacenet_root=satellite_root,
                isbda_root=drone_root,
                crisismmd_root=social_root,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.exception("Strict scenario readiness validation failed")
            return {
                "state": "unavailable",
                "valid": False,
                "message": "Strict scenario validation failed.",
                "action": "Verify mounted datasets, registered checksums and scenario manifests.",
            }
        return {
            "state": "healthy",
            "valid": True,
            "message": f"Strict scenario validation passed for {result['asset_count']} registered assets.",
            "action": None,
            "details": result,
        }

    def _create(self):
        spacenet, isbda = self._dataset_roots()
        if spacenet is None or isbda is None:
            raise ValueError("Configure the scenario satellite and drone dataset roots")
        validation = validate_scenario(
            self.scenario_path,
            strict=True,
            spacenet_root=spacenet,
            isbda_root=isbda,
            crisismmd_root=self._social_dataset_root(),
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
