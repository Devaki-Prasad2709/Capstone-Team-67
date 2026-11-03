"""Registered scenario packages and their immutable configuration."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_REGISTRY_PATH = Path(__file__).resolve().parents[1] / "scenarios" / "registry.json"


@dataclass(frozen=True)
class ScenarioRegistration:
    scenario_id: str
    title: str
    package_path: Path
    location: dict[str, Any]
    required_datasets: tuple[str, ...]
    enabled: bool
    dataset_roots: dict[str, str]

    @property
    def manifest_path(self) -> Path:
        return self.package_path / "scenario.json"

    @property
    def gis_path(self) -> Path:
        return self.package_path / "gis" / "infrastructure.geojson"

    def public(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "title": self.title,
            "location": self.location,
            "required_datasets": list(self.required_datasets),
            "enabled": self.enabled,
        }


class ScenarioRegistry:
    """Load and validate the small, version-controlled scenario catalogue."""

    def __init__(self, path: Path = DEFAULT_REGISTRY_PATH):
        self.path = Path(path).resolve()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        self.default_scenario_id = str(payload["default_scenario_id"])
        project_root = self.path.parents[1]
        entries: dict[str, ScenarioRegistration] = {}
        for raw in payload.get("scenarios", []):
            scenario_id = str(raw["scenario_id"])
            if scenario_id in entries:
                raise ValueError(f"Duplicate registered scenario: {scenario_id}")
            package = (project_root / str(raw["package_path"])).resolve()
            manifest = json.loads((package / "scenario.json").read_text(encoding="utf-8"))
            if manifest.get("scenario_id") != scenario_id:
                raise ValueError(f"Registry/manifest scenario ID mismatch for {scenario_id}")
            entries[scenario_id] = ScenarioRegistration(
                scenario_id=scenario_id,
                title=str(raw.get("title") or manifest.get("title") or scenario_id),
                package_path=package,
                location=dict(raw.get("location") or manifest.get("location") or {}),
                required_datasets=tuple(str(item) for item in raw.get("required_datasets", [])),
                enabled=bool(raw.get("enabled", False)),
                dataset_roots={str(k): str(v) for k, v in raw.get("dataset_roots", {}).items()},
            )
        if self.default_scenario_id not in entries:
            raise ValueError("Registry default_scenario_id is not registered")
        self._entries = entries

    def get(self, scenario_id: str | None = None) -> ScenarioRegistration:
        key = scenario_id or self.default_scenario_id
        try:
            entry = self._entries[key]
        except KeyError as exc:
            raise KeyError(f"Unknown scenario: {key}") from exc
        if not entry.enabled:
            raise ValueError(f"Scenario is disabled: {key}")
        return entry

    def list(self) -> list[ScenarioRegistration]:
        return list(self._entries.values())
