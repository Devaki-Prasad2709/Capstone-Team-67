"""Replay the frozen disaster story through the production Kafka contracts."""

from __future__ import annotations

import argparse
import json
import shlex
import time
from pathlib import Path

from config.settings import configure_logging, settings
from scenario_runtime import ScenarioController, ScenarioEventFactory, ScenarioKafkaDispatcher
from scripts.validate_scenario import validate_scenario


logger = configure_logging("scenario_runner")


def _project_path(raw: str) -> Path:
    path = Path(raw).expanduser()
    return path if path.is_absolute() else settings.project_root / path


def _print_status(controller: ScenarioController) -> None:
    status = controller.status()
    current = status["current_event"]
    print(json.dumps({
        "state": status["state"],
        "speed": status["speed"],
        "simulation_timestamp": status["simulation_timestamp"],
        "current_event": current["event_id"] if current else None,
        "completed": [event["event_id"] for event in status["completed_events"]],
        "upcoming": [event["event_id"] for event in status["upcoming_events"]],
        "error": status["error"],
    }, indent=2))


def _interactive(controller: ScenarioController) -> None:
    print("Commands: start, pause, resume, reset, speed <factor>, status, quit")
    while True:
        try:
            parts = shlex.split(input("scenario> ").strip())
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not parts:
            continue
        command = parts[0].lower()
        try:
            if command == "start":
                controller.start()
            elif command == "pause":
                controller.pause()
            elif command == "resume":
                controller.resume()
            elif command == "reset":
                controller.reset()
            elif command == "speed" and len(parts) == 2:
                controller.set_speed(float(parts[1]))
            elif command == "status":
                pass
            elif command in {"quit", "exit"}:
                return
            else:
                print("Commands: start, pause, resume, reset, speed <factor>, status, quit")
                continue
        except (RuntimeError, ValueError) as exc:
            print(f"Control rejected: {exc}")
        _print_status(controller)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", default=settings.final_scenario_path)
    parser.add_argument("--spacenet-root", default=settings.spacenet8_dataset_path)
    parser.add_argument("--isbda-root", default=settings.isbda_dataset_path)
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="open a prompt with start/pause/resume/reset/speed/status controls",
    )
    args = parser.parse_args()

    scenario_path = _project_path(args.scenario)
    if not args.spacenet_root or not args.isbda_root:
        parser.error("SPACENET8_DATASET_PATH and ISBDA_DATASET_PATH must be configured")
    spacenet_root = Path(args.spacenet_root).expanduser()
    isbda_root = Path(args.isbda_root).expanduser()
    validation = validate_scenario(
        scenario_path,
        strict=True,
        spacenet_root=spacenet_root,
        isbda_root=isbda_root,
    )
    logger.info("Strict scenario validation passed: %s", validation["scenario_id"])

    timeline_path = scenario_path.parent / "timeline.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    factory = ScenarioEventFactory(
        scenario_path,
        spacenet_root=spacenet_root,
        isbda_root=isbda_root,
    )
    dispatcher = ScenarioKafkaDispatcher(factory)
    controller = ScenarioController(
        timeline["events"],
        dispatcher,
        timeline["scenario_start"],
        speed=args.speed,
    )
    try:
        if args.interactive:
            _interactive(controller)
        else:
            controller.start()
            last_event = None
            while not controller.wait(timeout=0.25):
                current = controller.status()["current_event"]
                current_id = current["event_id"] if current else None
                if current_id != last_event:
                    _print_status(controller)
                    last_event = current_id
            _print_status(controller)
            if controller.status()["state"] == "failed":
                raise SystemExit(1)
    finally:
        dispatcher.close()


if __name__ == "__main__":
    main()
