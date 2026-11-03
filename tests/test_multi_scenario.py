from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashboard.map_api import map_config
from dashboard.scenario_service import DashboardScenarioService
from scenario_runtime import ScenarioRegistry
from scripts.validate_scenario import validate_scenario
from common.deduplication import ImageDeduplicator
from config.settings import settings
from scenario_runtime.publisher import ScenarioEventFactory
from core.satellite.change_contract import build_change_result
from data_source.satellite.satellite_producer import encode_tiff


ROOT = Path(__file__).parents[1]


def test_registry_preserves_louisiana_default_and_registers_mexico():
    registry = ScenarioRegistry(ROOT / "scenarios" / "registry.json")

    assert registry.default_scenario_id == "louisiana-east-flood-v1"
    assert registry.get().scenario_id == "louisiana-east-flood-v1"
    mexico = registry.get("mexico-earthquake-2017-v1")
    assert mexico.enabled is True
    assert mexico.required_datasets == ("xbd", "crisis_mmd", "isbda")
    assert mexico.manifest_path.is_file()
    assert mexico.gis_path.is_file()


def test_mexico_package_and_map_share_one_physical_footprint():
    registry = ScenarioRegistry(ROOT / "scenarios" / "registry.json")
    mexico = registry.get("mexico-earthquake-2017-v1")

    result = validate_scenario(mexico.manifest_path)
    config = map_config(mexico.manifest_path, mexico.gis_path)

    assert result["valid"] is True
    assert result["gis_ids"] == 29
    assert config["scenario"]["id"] == mexico.scenario_id
    assert config["scenario"]["bbox"] == mexico.location["bbox"]
    assert config["scenario"]["center"] == pytest.approx(mexico.location["center"])


def test_runner_instances_have_isolated_timeline_state():
    registry = ScenarioRegistry(ROOT / "scenarios" / "registry.json")
    louisiana = DashboardScenarioService(registry.get().manifest_path)
    mexico = DashboardScenarioService(registry.get("mexico-earthquake-2017-v1").manifest_path)

    louisiana.control("speed", 2)
    mexico.control("speed", 10)

    assert louisiana.status()["scenario_id"] == "louisiana-east-flood-v1"
    assert mexico.status()["scenario_id"] == "mexico-earthquake-2017-v1"
    assert louisiana.status()["speed"] == 2
    assert mexico.status()["speed"] == 10
    assert louisiana.status()["upcoming_events"][0]["scenario_timestamp"] != mexico.status()["upcoming_events"][0]["scenario_timestamp"]


def test_mexico_strict_external_provenance_when_dataset_drive_is_mounted():
    roots = [Path(settings.xbd_dataset_path), Path(settings.isbda_dataset_path), Path(settings.crisismmd_dataset_path)]
    if not all(path.is_dir() for path in roots):
        pytest.skip("External xBD, ISBDA, and CrisisMMD dataset roots are not mounted")
    mexico = ScenarioRegistry(ROOT / "scenarios" / "registry.json").get("mexico-earthquake-2017-v1")
    result = validate_scenario(
        mexico.manifest_path,
        strict=True,
        spacenet_root=roots[0],
        isbda_root=roots[1],
        crisismmd_root=roots[2],
    )
    assert set(result["verified_external_sources"]) == {"xbd", "isbda", "crisis_mmd"}

    pair = json.loads((mexico.package_path / "satellite" / "pair.json").read_text(encoding="utf-8"))
    events = []
    for phase in ("pre", "post"):
        definition = pair[f"{phase}_event"]
        encoded = encode_tiff(roots[0] / definition["relative_path"], 85)
        assert encoded is not None
        events.append({
            "image_id": phase,
            "timestamp": 1.0 if phase == "pre" else 2.0,
            "source": "satellite",
            "transfer_mode": "test",
            "satellite_phase": phase,
            "tile_id": pair["tile_id"],
            "bbox": definition["bbox"],
            "scenario_id": pair["scenario_id"],
            "scenario_timestamp": definition["scenario_timestamp"],
            "payload": encoded[1],
            "reference_feature_count": pair["reference"]["feature_count"],
            "reference_damage_counts": pair["reference"]["damage_counts"],
        })
    satellite_result = build_change_result(events[0], events[1], lambda event: event["payload"])
    assert satellite_result["summary"]["scored_cell_count"] == 64
    assert satellite_result["summary"]["changed_cell_count"] > 0
    assert satellite_result["reference_labels"]["damage_counts"]["major-damage"] == 16
    assert satellite_result["tgnn_integration"] == "none"


def test_dashboard_context_switch_replaces_every_scenario_scoped_service():
    import dashboard.server as server

    original_id = server.active_registration.scenario_id
    try:
        server._activate_scenario_context("mexico-earthquake-2017-v1")
        assert server.active_registration.scenario_id == "mexico-earthquake-2017-v1"
        assert server.operational_dashboard.scenario["scenario_id"] == "mexico-earthquake-2017-v1"
        assert server.scenario_runner.status()["scenario_id"] == "mexico-earthquake-2017-v1"
        assert server.scenario_social_alerts.gis_path == server.active_registration.gis_path
        assert server.building_classifications.gis_path == server.active_registration.gis_path
        assert "mexico-earthquake-2017-v1" in server.building_classifications.store.database_path.name
        mexico_snapshot = server.operational_dashboard.snapshot([], [])
        assert mexico_snapshot["counts"]["structural_features"] == 29
    finally:
        server._activate_scenario_context(original_id)

    assert server.active_registration.scenario_id == original_id
    assert server.operational_dashboard.scenario["scenario_id"] == original_id
    assert server.scenario_runner.status()["state"] == "idle"


def test_scenario_activation_restarts_dashboard_owned_observation_worker(monkeypatch):
    import dashboard.server as server

    original_id = server.active_registration.scenario_id
    target_id = (
        "mexico-earthquake-2017-v1"
        if original_id != "mexico-earthquake-2017-v1"
        else "louisiana-east-flood-v1"
    )

    class ManagedObservationStub:
        def __init__(self):
            self.running = True
            self.started_commands = []

        def status(self, name):
            assert name == "observation"
            return {
                "state": "running" if self.running else "stopped",
                "ownership": "dashboard" if self.running else "none",
            }

        def stop(self, name):
            assert name == "observation"
            self.running = False
            return True, "stopped"

        def start(self, name, command):
            assert name == "observation"
            self.running = True
            self.started_commands.append(command)
            return True, "started"

    managed = ManagedObservationStub()
    monkeypatch.setattr(server, "manager", managed)
    try:
        result = server.activate_scenario(target_id)
        target = server.scenario_registry.get(target_id)
        command = managed.started_commands[-1]
        assert result["scenario"]["scenario_id"] == target_id
        assert str(target.gis_path) in command
        assert f"disaster-observation-dashboard-{target_id}" in command
    finally:
        server._activate_scenario_context(original_id)


def test_mexico_factory_preserves_earthquake_and_xbd_provenance(tmp_path):
    package = ROOT / "scenarios" / "mexico_earthquake_2017"
    pair = json.loads((package / "satellite" / "pair.json").read_text(encoding="utf-8"))
    selection = json.loads((package / "drone" / "selection.json").read_text(encoding="utf-8"))
    satellite_root, drone_root = tmp_path / "xbd", tmp_path / "isbda"
    for phase in ("pre_event", "post_event"):
        path = satellite_root / pair[phase]["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    for image in selection["images"]:
        path = drone_root / image["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")

    def satellite_builder(path, _quality, _dedup, _transfer):
        return {"image_id": path.name, "timestamp": 0.0, "source": "satellite", "data_type": "image", "transfer_mode": "base64", "image_data": "dGVzdA=="}

    factory = ScenarioEventFactory(
        package / "scenario.json",
        spacenet_root=satellite_root,
        isbda_root=drone_root,
        deduplicator=ImageDeduplicator(tmp_path / "dedup.sqlite3"),
        transfer=object(),
        satellite_builder=satellite_builder,
    )
    satellite = factory.satellite_pair()
    social = factory.social("social-001")

    assert all(event["input_origin"] == "real-xbd" for event in satellite)
    assert satellite[1]["reference_damage_counts"]["major-damage"] == 16
    assert social["hazard"] == "earthquake"
    assert social["input_origin"] == "hybrid-real-crisismmd-simulated-context"
    assert set(social["simulation_fields"]) == {"scenario_timestamp", "gps", "target_ids"}
