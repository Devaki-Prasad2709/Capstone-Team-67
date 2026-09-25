"""Thread-safe, speed-adjustable controller for a frozen scenario timeline."""

from __future__ import annotations

import copy
import math
import threading
import time
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable, Iterable


class RunnerState(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


class InvalidRunnerTransition(RuntimeError):
    """Raised when a control is not valid for the current runner state."""


class ScenarioController:
    """Drive ordered events using simulated time without knowing their transport.

    ``dispatcher`` is the only side-effect boundary. Production supplies the
    Kafka/core dispatcher; tests supply an in-memory recorder. The controller
    never writes dashboard results or model outputs.
    """

    def __init__(
        self,
        events: Iterable[dict],
        dispatcher: Callable[[dict], None],
        scenario_start: str,
        *,
        speed: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        poll_interval: float = 0.05,
    ) -> None:
        self._events = sorted((copy.deepcopy(event) for event in events), key=lambda x: x["offset_seconds"])
        if not self._events:
            raise ValueError("ScenarioController requires at least one event")
        offsets = [event.get("offset_seconds") for event in self._events]
        if any(not isinstance(value, (int, float)) or value < 0 for value in offsets):
            raise ValueError("Scenario offsets must be non-negative numbers")
        if offsets != sorted(set(offsets)):
            raise ValueError("Scenario offsets must be unique and strictly increasing")
        self._scenario_start = datetime.fromisoformat(scenario_start.replace("Z", "+00:00"))
        self._dispatcher = dispatcher
        self._clock = clock
        self._poll_interval = max(float(poll_interval), 0.001)
        self._lock = threading.RLock()
        self._state = RunnerState.IDLE
        self._speed = self._valid_speed(speed)
        self._position = 0.0
        self._anchor = self._clock()
        self._next_index = 0
        self._completed: list[dict] = []
        self._current: dict | None = None
        self._error: str | None = None
        self._generation = 0
        self._thread: threading.Thread | None = None

    @staticmethod
    def _valid_speed(value: float) -> float:
        speed = float(value)
        if not math.isfinite(speed) or speed <= 0:
            raise ValueError("Playback speed must be a positive finite number")
        return speed

    def _elapsed_locked(self) -> float:
        if self._state == RunnerState.RUNNING:
            return self._position + max(0.0, self._clock() - self._anchor) * self._speed
        return self._position

    def _materialize_locked(self) -> None:
        self._position = self._elapsed_locked()
        self._anchor = self._clock()

    def start(self, *, background: bool = True) -> None:
        with self._lock:
            if self._state != RunnerState.IDLE:
                raise InvalidRunnerTransition(f"Cannot start from {self._state.value}")
            self._state = RunnerState.RUNNING
            self._anchor = self._clock()
            self._error = None
            self._generation += 1
            generation = self._generation
            if background:
                self._thread = threading.Thread(
                    target=self._worker,
                    args=(generation,),
                    name="scenario-runner",
                    daemon=True,
                )
                self._thread.start()

    def pause(self) -> None:
        with self._lock:
            if self._state != RunnerState.RUNNING:
                raise InvalidRunnerTransition(f"Cannot pause from {self._state.value}")
            self._materialize_locked()
            self._state = RunnerState.PAUSED

    def resume(self) -> None:
        with self._lock:
            if self._state != RunnerState.PAUSED:
                raise InvalidRunnerTransition(f"Cannot resume from {self._state.value}")
            self._anchor = self._clock()
            self._state = RunnerState.RUNNING

    def reset(self) -> None:
        with self._lock:
            self._generation += 1
            self._state = RunnerState.IDLE
            self._position = 0.0
            self._anchor = self._clock()
            self._next_index = 0
            self._completed.clear()
            self._current = None
            self._error = None

    def set_speed(self, speed: float) -> None:
        value = self._valid_speed(speed)
        with self._lock:
            if self._state == RunnerState.RUNNING:
                self._materialize_locked()
            self._speed = value

    def tick(self, *, generation: int | None = None) -> int:
        """Publish all events due at the current simulated time."""
        published = 0
        while True:
            with self._lock:
                if generation is not None and generation != self._generation:
                    return published
                if self._state != RunnerState.RUNNING:
                    return published
                simulated = self._elapsed_locked()
                if self._next_index >= len(self._events):
                    self._materialize_locked()
                    self._state = RunnerState.COMPLETED
                    return published
                event = self._events[self._next_index]
                if event["offset_seconds"] > simulated:
                    return published
                self._next_index += 1
                self._current = copy.deepcopy(event)
                event_generation = self._generation
            try:
                self._dispatcher(copy.deepcopy(event))
            except Exception as exc:
                with self._lock:
                    if event_generation == self._generation:
                        self._materialize_locked()
                        self._state = RunnerState.FAILED
                        self._error = f"{type(exc).__name__}: {exc}"
                return published
            with self._lock:
                if event_generation != self._generation:
                    return published
                self._completed.append(copy.deepcopy(event))
                published += 1
                if self._next_index >= len(self._events):
                    self._materialize_locked()
                    self._state = RunnerState.COMPLETED
                    return published

    def _worker(self, generation: int) -> None:
        while True:
            self.tick(generation=generation)
            with self._lock:
                if generation != self._generation or self._state in {
                    RunnerState.IDLE,
                    RunnerState.COMPLETED,
                    RunnerState.FAILED,
                }:
                    return
            time.sleep(self._poll_interval)

    def wait(self, timeout: float | None = None) -> bool:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
            return not thread.is_alive()
        return self._state in {RunnerState.COMPLETED, RunnerState.FAILED}

    def status(self) -> dict:
        with self._lock:
            elapsed = self._elapsed_locked()
            timestamp = self._scenario_start + timedelta(seconds=elapsed)
            return {
                "state": self._state.value,
                "speed": self._speed,
                "simulation_elapsed_seconds": elapsed,
                "simulation_timestamp": timestamp.isoformat().replace("+00:00", "Z"),
                "current_event": copy.deepcopy(self._current),
                "completed_events": copy.deepcopy(self._completed),
                "upcoming_events": copy.deepcopy(self._events[self._next_index:]),
                "error": self._error,
            }
