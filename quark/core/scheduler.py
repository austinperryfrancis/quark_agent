"""DAG scheduling with bounded retries and serialized model execution."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from threading import Event, Lock
from time import monotonic
from typing import Any

from quark.capabilities.registry import CapabilityRegistry
from quark.state.database import StateDatabase
from quark.state.models import GoalStatus, StepStatus


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    value: Any
    validation_ok: bool = True
    validation: Any | None = None
    duration_ms: float | None = None


Executor = Callable[[sqlite3.Row], Any]


class Scheduler:
    """Execute persisted ready steps, parallelizing deterministic work only."""

    def __init__(
        self,
        state: StateDatabase,
        registry: CapabilityRegistry,
        *,
        max_workers: int = 1,
    ) -> None:
        self.state = state
        self.registry = registry
        self.max_workers = max(1, max_workers)
        self._model_lock = Lock()

    def _run_one(self, step: sqlite3.Row, executor: Executor) -> ExecutionResult:
        metadata = self.registry.get(str(step["operation"]))
        started = monotonic()
        if metadata.model_required:
            with self._model_lock:
                value = executor(step)
        else:
            value = executor(step)
        duration = (monotonic() - started) * 1000
        if isinstance(value, ExecutionResult):
            if value.duration_ms is None:
                return ExecutionResult(
                    value.value, value.validation_ok, value.validation, duration
                )
            return value
        return ExecutionResult(value, duration_ms=duration)

    def run(
        self,
        goal_id: int,
        executor: Executor,
        *,
        cancel_event: Event | None = None,
    ) -> GoalStatus:
        """Run until the goal has no executable work, resuming persisted steps."""
        while True:
            if cancel_event is not None and cancel_event.is_set():
                self.state.set_goal_status(goal_id, GoalStatus.CANCELLED)
                return GoalStatus.CANCELLED
            steps: list[sqlite3.Row] = []
            for _ in range(self.max_workers):
                step = self.state.claim_ready_step(goal_id)
                if step is None:
                    break
                steps.append(step)
            if not steps:
                counts = self.state.goal_step_counts(goal_id)
                if counts.get(StepStatus.FAILED.value, 0):
                    self.state.set_goal_status(goal_id, GoalStatus.FAILED)
                    return GoalStatus.FAILED
                if counts.get(StepStatus.BLOCKED.value, 0):
                    self.state.set_goal_status(goal_id, GoalStatus.BLOCKED)
                    return GoalStatus.BLOCKED
                if counts and all(
                    status == StepStatus.COMPLETE.value for status in counts
                ):
                    self.state.set_goal_status(goal_id, GoalStatus.COMPLETE)
                    return GoalStatus.COMPLETE
                if (
                    counts.get(StepStatus.RUNNING.value, 0) == 0
                    and not counts.get(StepStatus.PENDING.value, 0)
                    and not counts.get(StepStatus.READY.value, 0)
                ):
                    self.state.set_goal_status(goal_id, GoalStatus.BLOCKED)
                    return GoalStatus.BLOCKED
                continue

            with ThreadPoolExecutor(max_workers=len(steps)) as pool:
                futures = {
                    pool.submit(self._run_one, step, executor): step for step in steps
                }
                for future, step in futures.items():
                    metadata = self.registry.get(str(step["operation"]))
                    try:
                        result = future.result(timeout=metadata.timeout_seconds)
                    except Exception as error:
                        attempt = int(step["attempt"])
                        self.state.record_error(
                            type(error).__name__,
                            str(error),
                            goal_id=goal_id,
                            step_id=int(step["id"]),
                            attempt=attempt,
                        )
                        metadata = self.registry.get(str(step["operation"]))
                        if attempt < metadata.max_attempts:
                            self.state.retry_step(int(step["id"]))
                        else:
                            self.state.fail_step(int(step["id"]))
                            self.state.set_goal_status(goal_id, GoalStatus.FAILED)
                    else:
                        if not result.validation_ok:
                            self.state.record_error(
                                "ValidationError",
                                "step result failed validation",
                                goal_id=goal_id,
                                step_id=int(step["id"]),
                                attempt=int(step["attempt"]),
                            )
                            if int(step["attempt"]) < metadata.max_attempts:
                                self.state.retry_step(int(step["id"]))
                            else:
                                self.state.fail_step(int(step["id"]))
                        else:
                            self.state.complete_step(
                                int(step["id"]),
                                result.value,
                                validation_ok=True,
                                validation=result.validation,
                                duration_ms=result.duration_ms,
                            )
