"""Persistent state models and workflow statuses."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class GoalStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    RETRYING = "retrying"
    BLOCKED = "blocked"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class Goal:
    id: int
    goal_type: str
    target: str | None
    status: GoalStatus
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Step:
    id: int
    goal_id: int
    step_key: str
    operation: str
    status: StepStatus
    attempt: int
    input_digest: str | None
    input_ref: str | None


@dataclass(frozen=True, slots=True)
class StepResult:
    id: int
    step_id: int
    result_json: str | None
    result_digest: str | None
    validation_ok: bool
    validation_json: str | None
    duration_ms: float | None
    created_at: datetime


def enum_value(value: str | StrEnum) -> str:
    """Return a database-safe string for a status or enum."""
    return value.value if isinstance(value, StrEnum) else value
