"""Serializable domain objects for Quark's execution state."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


class SideEffect(StrEnum):
    READ_ONLY = "READ_ONLY"
    LOCAL_WRITE = "LOCAL_WRITE"
    EXTERNAL_WRITE = "EXTERNAL_WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"


class ReviewPolicy(StrEnum):
    NEVER = "NEVER"
    OPTIONAL = "OPTIONAL"
    ALWAYS = "ALWAYS"
    RISK_BASED = "RISK_BASED"


class SkillCallStatus(StrEnum):
    CREATED = "CREATED"
    PROPOSED = "PROPOSED"
    SCHEMA_VALIDATING = "SCHEMA_VALIDATING"
    VALIDATING = "VALIDATING"
    AWAITING_INPUT = "AWAITING_INPUT"
    AWAITING_REVIEW = "AWAITING_REVIEW"
    REVISED = "REVISED"
    APPROVED = "APPROVED"
    EXECUTING = "EXECUTING"
    VALIDATING_RESULT = "VALIDATING_RESULT"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class RunStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class InteractionType(StrEnum):
    ASK_USER = "ASK_USER"
    REVIEW_CALL = "REVIEW_CALL"
    REVIEW_RESULT = "REVIEW_RESULT"


class EventType(StrEnum):
    RUN_CREATED = "RUN_CREATED"
    CALL_PROPOSED = "CALL_PROPOSED"
    CALL_VALIDATED = "CALL_VALIDATED"
    CALL_EDITED = "CALL_EDITED"
    CALL_REVIEW_REQUESTED = "CALL_REVIEW_REQUESTED"
    CALL_APPROVED = "CALL_APPROVED"
    CALL_STARTED = "CALL_STARTED"
    CALL_COMPLETED = "CALL_COMPLETED"
    CALL_FAILED = "CALL_FAILED"
    CALL_REJECTED = "CALL_REJECTED"
    CALL_CANCELLED = "CALL_CANCELLED"
    CALL_INTERRUPTED = "CALL_INTERRUPTED"
    CALL_RETRY_REQUESTED = "CALL_RETRY_REQUESTED"
    RUN_INTERRUPTED = "RUN_INTERRUPTED"
    USER_QUESTION_REQUESTED = "USER_QUESTION_REQUESTED"
    USER_RESPONSE_RECEIVED = "USER_RESPONSE_RECEIVED"


class InvalidStateTransitionError(ValueError):
    pass


_SKILL_CALL_TRANSITIONS: dict[SkillCallStatus, frozenset[SkillCallStatus]] = {
    SkillCallStatus.CREATED: frozenset(
        {SkillCallStatus.PROPOSED, SkillCallStatus.CANCELLED}
    ),
    SkillCallStatus.PROPOSED: frozenset(
        {
            SkillCallStatus.SCHEMA_VALIDATING,
            SkillCallStatus.REJECTED,
            SkillCallStatus.CANCELLED,
            SkillCallStatus.FAILED,
            SkillCallStatus.INTERRUPTED,
        }
    ),
    SkillCallStatus.SCHEMA_VALIDATING: frozenset(
        {
            SkillCallStatus.VALIDATING,
            SkillCallStatus.AWAITING_REVIEW,
            SkillCallStatus.EXECUTING,
            SkillCallStatus.FAILED,
            SkillCallStatus.INTERRUPTED,
        }
    ),
    SkillCallStatus.VALIDATING: frozenset(
        {
            SkillCallStatus.AWAITING_INPUT,
            SkillCallStatus.AWAITING_REVIEW,
            SkillCallStatus.APPROVED,
            SkillCallStatus.EXECUTING,
            SkillCallStatus.REVISED,
            SkillCallStatus.REJECTED,
            SkillCallStatus.FAILED,
            SkillCallStatus.INTERRUPTED,
        }
    ),
    SkillCallStatus.AWAITING_INPUT: frozenset(
        {
            SkillCallStatus.REVISED,
            SkillCallStatus.EXECUTING,
            SkillCallStatus.REJECTED,
            SkillCallStatus.CANCELLED,
            SkillCallStatus.FAILED,
        }
    ),
    SkillCallStatus.AWAITING_REVIEW: frozenset(
        {
            SkillCallStatus.REVISED,
            SkillCallStatus.APPROVED,
            SkillCallStatus.EXECUTING,
            SkillCallStatus.COMPLETED,
            SkillCallStatus.REJECTED,
            SkillCallStatus.CANCELLED,
            SkillCallStatus.FAILED,
        }
    ),
    SkillCallStatus.REVISED: frozenset(
        {
            SkillCallStatus.SCHEMA_VALIDATING,
            SkillCallStatus.CANCELLED,
            SkillCallStatus.INTERRUPTED,
        }
    ),
    SkillCallStatus.APPROVED: frozenset(
        {
            SkillCallStatus.EXECUTING,
            SkillCallStatus.CANCELLED,
            SkillCallStatus.INTERRUPTED,
        }
    ),
    SkillCallStatus.EXECUTING: frozenset(
        {
            SkillCallStatus.AWAITING_INPUT,
            SkillCallStatus.AWAITING_REVIEW,
            SkillCallStatus.VALIDATING_RESULT,
            SkillCallStatus.COMPLETED,
            SkillCallStatus.FAILED,
            SkillCallStatus.CANCELLED,
            SkillCallStatus.INTERRUPTED,
        }
    ),
    SkillCallStatus.VALIDATING_RESULT: frozenset(
        {
            SkillCallStatus.AWAITING_INPUT,
            SkillCallStatus.AWAITING_REVIEW,
            SkillCallStatus.EXECUTING,
            SkillCallStatus.COMPLETED,
            SkillCallStatus.FAILED,
            SkillCallStatus.INTERRUPTED,
        }
    ),
    SkillCallStatus.COMPLETED: frozenset(),
    SkillCallStatus.FAILED: frozenset(),
    SkillCallStatus.REJECTED: frozenset(),
    SkillCallStatus.CANCELLED: frozenset(),
    SkillCallStatus.INTERRUPTED: frozenset(
        {SkillCallStatus.EXECUTING, SkillCallStatus.CANCELLED}
    ),
}


class SkillError(BaseModel):
    """A stable, machine-readable Skill failure."""

    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    recoverable: bool = False
    details: dict[str, Any] | None = None


class SkillCallRevision(BaseModel):
    """An immutable snapshot of arguments proposed for a SkillCall."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: int = Field(ge=1)
    arguments: dict[str, Any]
    source: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class SkillCall(BaseModel):
    """One concrete and auditable proposed Skill execution."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=lambda: new_id("call"))
    run_id: str = Field(min_length=1)
    skill_name: str = Field(min_length=1)
    arguments: dict[str, Any]
    parent_call_id: str | None = None
    depth: int = Field(default=0, ge=0)
    status: SkillCallStatus = SkillCallStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    proposed_by: str = Field(default="runtime", min_length=1)
    validation_status: str | None = None
    validation_reason: str | None = None
    validation_question: str | None = None
    review_status: str | None = None
    result: Any | None = None
    error: SkillError | None = None
    revisions: list[SkillCallRevision] = Field(default_factory=list)

    @model_validator(mode="after")
    def add_initial_revision(self) -> SkillCall:
        if not self.revisions:
            self.revisions.append(
                SkillCallRevision(
                    revision=1,
                    arguments=deepcopy(self.arguments),
                    source=self.proposed_by,
                    created_at=self.created_at,
                )
            )
        return self

    def transition(self, status: SkillCallStatus) -> None:
        if status not in _SKILL_CALL_TRANSITIONS[self.status]:
            raise InvalidStateTransitionError(
                f"SkillCall cannot transition from {self.status} to {status}"
            )
        self.status = status
        self.updated_at = utc_now()

    def revise(self, arguments: dict[str, Any], *, source: str) -> None:
        self.arguments = deepcopy(arguments)
        self.revisions.append(
            SkillCallRevision(
                revision=len(self.revisions) + 1,
                arguments=deepcopy(arguments),
                source=source,
            )
        )
        self.transition(SkillCallStatus.REVISED)


class Run(BaseModel):
    """A top-level user task containing a tree of SkillCalls."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=lambda: new_id("run"))
    session_id: str = Field(min_length=1)
    original_request: str
    root_skill_call_id: str | None = None
    status: RunStatus = RunStatus.CREATED
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)
    completed_at: datetime | None = None


class PendingInteraction(BaseModel):
    """A persisted conversational decision associated with a Run or SkillCall."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("interaction"))
    type: InteractionType
    run_id: str = Field(min_length=1)
    skill_call_id: str | None = None
    original_request: str
    question: str | None = None
    proposed_call: SkillCall | None = None
    proposed_result: Any | None = None
    created_at: datetime = Field(default_factory=utc_now)


class Session(BaseModel):
    """Compact conversational state, separate from model context."""

    model_config = ConfigDict(extra="forbid", validate_assignment=True)

    id: str = Field(default_factory=lambda: new_id("session"))
    session_key: str = Field(min_length=1)
    active_run_id: str | None = None
    pending_interaction: PendingInteraction | None = None
    recent_user_request: str | None = None
    recent_quark_response: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)


class ExecutionEvent(BaseModel):
    """An append-only fact about runtime execution."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(default_factory=lambda: new_id("event"))
    type: EventType
    run_id: str
    skill_call_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utc_now)
