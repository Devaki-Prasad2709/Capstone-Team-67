from __future__ import annotations

import json
from pathlib import Path

import networkx as nx
import pytest

from common.deduplication import ImageDeduplicator
from common.topics import DRONE_TOPIC, SATELLITE_TOPIC, SOCIAL_TOPIC, TELEMETRY_TOPIC
from core.observation.telemetry import apply_telemetry_event, validate_telemetry_event
from scenario_runtime.controller import InvalidRunnerTransition, ScenarioController
from scenario_runtime.publisher import ScenarioEventFactory, ScenarioKafkaDispatcher


SCENARIO = Path(__file__).parents[1] / "scenarios" / "louisiana_east_flood"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _event(sequence: int, offset: int) -> dict:
    return {
        "sequence": sequence,
        "event_id": f"event-{sequence}",
        "name": f"event_{sequence}",
        "offset_seconds": offset,
    }


def test_controller_controls_simulated_time_and_event_queues():
    clock = FakeClock()
    published = []
    controller = ScenarioController(
        [_event(1, 0), _event(2, 10), _event(3, 20)],
        published.append,
        "2026-09-24T12:00:00Z",
        speed=2,
        clock=clock,
    )

    controller.start(background=False)
    assert controller.tick() == 1
    assert [item["event_id"] for item in published] == ["event-1"]

    clock.advance(4)
    assert controller.tick() == 0
    controller.pause()
    paused_at = controller.status()["simulation_elapsed_seconds"]
    clock.advance(100)
    assert controller.status()["simulation_elapsed_seconds"] == paused_at
    with pytest.raises(InvalidRunnerTransition):
        controller.pause()

    controller.set_speed(4)
    controller.resume()
    clock.advance(3)
    assert controller.tick() == 2
    status = controller.status()
    assert status["state"] == "completed"
    assert len(status["completed_events"]) == 3
    assert status["upcoming_events"] == []

    controller.reset()
    status = controller.status()
    assert status["state"] == "idle"
    assert status["current_event"] is None
    assert len(status["upcoming_events"]) == 3


def test_controller_surfaces_dispatch_failure():
    def fail(_event):
        raise RuntimeError("transport unavailable")

    controller = ScenarioController(
        [_event(1, 0)], fail, "2026-09-24T12:00:00Z", clock=FakeClock()
    )
    controller.start(background=False)
    assert controller.tick() == 0
    assert controller.status()["state"] == "failed"
    assert "transport unavailable" in controller.status()["error"]


def _touch_registered_assets(root: Path, metadata: dict, keys: tuple[str, ...]) -> None:
    for key in keys:
        path = root / metadata[key]["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")


def test_event_factory_keeps_existing_wire_contracts(tmp_path):
    satellite = json.loads((SCENARIO / "satellite" / "pair.json").read_text(encoding="utf-8"))
    drone = json.loads((SCENARIO / "drone" / "selection.json").read_text(encoding="utf-8"))
    spacenet_root = tmp_path / "spacenet"
    isbda_root = tmp_path / "isbda"
    _touch_registered_assets(spacenet_root, satellite, ("pre_event", "post_event"))
    for asset in drone["images"]:
        path = isbda_root / asset["relative_path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")

    def fake_drone(path, _dedup, _transfer):
        return {
            "frame_id": path.name,
            "timestamp": 0.0,
            "source": "drone",
            "data_type": "image",
            "format": "jpg",
            "transfer_mode": "base64",
            "image_data": "dGVzdA==",
        }

    def fake_satellite(path, _quality, _dedup, _transfer):
        return {
            "image_id": path.name,
            "original_format": "tif",
            "stream_format": "jpg",
            "timestamp": 0.0,
            "source": "satellite",
            "data_type": "image",
            "transfer_mode": "base64",
            "image_data": "dGVzdA==",
        }

    factory = ScenarioEventFactory(
        SCENARIO / "scenario.json",
        spacenet_root=spacenet_root,
        isbda_root=isbda_root,
        deduplicator=ImageDeduplicator(tmp_path / "dedup.sqlite3"),
        transfer=object(),
        drone_builder=fake_drone,
        satellite_builder=fake_satellite,
    )
    drone_event = factory.drone("drone-001")
    assert {"frame_id", "timestamp", "source", "data_type", "format", "image_data"} <= drone_event.keys()
    assert drone_event["target_id"] == "osm-way-791288888"
    satellite_events = factory.satellite_pair()
    assert [event["satellite_phase"] for event in satellite_events] == ["pre", "post"]
    assert all("image_id" in event and event["source"] == "satellite" for event in satellite_events)
    social_event = factory.social("social-001")
    assert {"id", "text", "hazard", "event", "timestamp", "source", "data_type"} <= social_event.keys()
    assert social_event["review_status"] == "PENDING_REVIEW"
    telemetry_event = factory.telemetry("telemetry-001")
    validate_telemetry_event(telemetry_event)
    assert telemetry_event["source"] == "infrastructure_telemetry"


class FakeProducer:
    def flush(self, timeout):
        self.flush_timeout = timeout

    def close(self):
        self.closed = True


class FakeFactory:
    def satellite_pair(self):
        return [{"scenario_event_id": "satellite-pre"}, {"scenario_event_id": "satellite-post"}]

    def drone(self, event_id):
        return {"scenario_event_id": event_id}

    def social(self, event_id):
        return {"scenario_event_id": event_id}

    def telemetry(self, event_id):
        return {"scenario_event_id": event_id}


def test_dispatcher_routes_only_real_input_interfaces():
    sent = []

    def sender(_producer, topic, payload, _logger):
        sent.append((topic, payload["scenario_event_id"]))
        return True

    dispatcher = ScenarioKafkaDispatcher(FakeFactory(), producer=FakeProducer(), sender=sender)
    timeline = json.loads((SCENARIO / "timeline.json").read_text(encoding="utf-8"))["events"]
    for event in timeline:
        dispatcher(event)

    assert [topic for topic, _ in sent].count(SATELLITE_TOPIC) == 2
    assert [topic for topic, _ in sent].count(DRONE_TOPIC) == 3
    assert [topic for topic, _ in sent].count(SOCIAL_TOPIC) == 1
    assert [topic for topic, _ in sent].count(TELEMETRY_TOPIC) == 2
    assert len(sent) == 8


def test_telemetry_adapter_preserves_topology_and_damage_during_recovery():
    graph = nx.Graph()
    graph.add_node(0, load=0.1, capacity=1.0, damage=0.0)
    graph.add_node(1, load=0.2, capacity=1.0, damage=0.0)
    graph.add_edge(0, 1, edge_type=0)
    id_map = {"road": 0, "building": 1}
    degraded = apply_telemetry_event(graph, id_map, {
        "id": "telemetry-001", "timestamp": 1.0, "target_id": "road",
        "load": 0.72, "capacity": 0.8, "damage": 0.35,
    })
    recovered = apply_telemetry_event(degraded, id_map, {
        "id": "telemetry-002", "timestamp": 2.0, "target_id": "road",
        "load": 0.44, "capacity": 0.8, "damage": 0.15,
    })
    assert set(recovered.edges) == set(graph.edges)
    assert recovered.nodes[0]["load"] == 0.44
    assert recovered.nodes[0]["damage"] == 0.35
    assert graph.nodes[0]["damage"] == 0.0
