from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Callable


class StageTimeout(AssertionError):
    pass


@dataclass(frozen=True)
class StageRecord:
    operation: str
    stage: str
    context: dict[str, Any]
    monotonic_ns: int


class _Pause:
    def __init__(self) -> None:
        self.reached = threading.Event()
        self.release = threading.Event()


class DeterministicStageController:
    """Tiny deterministic interleaving harness for persistence fault tests.

    Production code exposes semantic stage hooks at persistence boundaries. Tests can
    pause one operation at an exact named stage, run another operation to completion,
    then resume the first. No sleeps are needed, which keeps race tests deterministic
    and inside Breadmaker's hard per-test deadline.

    The controller owns no storage state and cannot make a stage authoritative. It is
    merely a synchronization observer used by reliability tests and diagnostics tools.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pauses: dict[tuple[str, str], _Pause] = {}
        self._trace: list[StageRecord] = []

    def pause(self, operation: str, stage: str) -> None:
        key = (str(operation), str(stage))
        with self._lock:
            if key in self._pauses:
                raise ValueError(f'DETERMINISTIC_STAGE_ALREADY_PAUSED:{operation}:{stage}')
            self._pauses[key] = _Pause()

    def hook(self, operation: str) -> Callable[..., None]:
        operation = str(operation)

        def _hook(stage: str, context: dict[str, Any] | None = None) -> None:
            key = (operation, str(stage))
            record = StageRecord(operation, str(stage), dict(context or {}), time.monotonic_ns())
            with self._lock:
                self._trace.append(record)
                pause = self._pauses.get(key)
            if pause is None:
                return
            pause.reached.set()
            if not pause.release.wait(5):
                raise StageTimeout(f'DETERMINISTIC_STAGE_RELEASE_TIMEOUT:{operation}:{stage}')

        return _hook

    def wait_until(self, operation: str, stage: str, timeout: float = 2.0) -> StageRecord:
        key = (str(operation), str(stage))
        with self._lock:
            pause = self._pauses.get(key)
        if pause is None:
            raise ValueError(f'DETERMINISTIC_STAGE_NOT_PAUSED:{operation}:{stage}')
        if not pause.reached.wait(float(timeout)):
            raise StageTimeout(f'DETERMINISTIC_STAGE_NOT_REACHED:{operation}:{stage}')
        with self._lock:
            for record in reversed(self._trace):
                if (record.operation, record.stage) == key:
                    return record
        raise StageTimeout(f'DETERMINISTIC_STAGE_TRACE_MISSING:{operation}:{stage}')

    def release(self, operation: str, stage: str) -> None:
        key = (str(operation), str(stage))
        with self._lock:
            pause = self._pauses.get(key)
        if pause is None:
            raise ValueError(f'DETERMINISTIC_STAGE_NOT_PAUSED:{operation}:{stage}')
        pause.release.set()

    def trace(self) -> tuple[StageRecord, ...]:
        with self._lock:
            return tuple(self._trace)
