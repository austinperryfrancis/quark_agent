"""Schemas shared by narrow Quark inference tasks."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InferenceTaskType(StrEnum):
    ROUTE_SKILL = "ROUTE_SKILL"
    GENERATE_ARGUMENTS = "GENERATE_ARGUMENTS"
    VALIDATE_CALL = "VALIDATE_CALL"
    EDIT_ARGUMENTS = "EDIT_ARGUMENTS"
    CLASSIFY_RESPONSE = "CLASSIFY_RESPONSE"
    SELECT_CHILD = "SELECT_CHILD"
    VALIDATE_RESULT = "VALIDATE_RESULT"


class MessageRole(StrEnum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class Message(StrictModel):
    role: MessageRole
    content: str


class RouteDecision(StrictModel):
    skill: str | None = None
    unsupported: bool = False

    @model_validator(mode="after")
    def require_exactly_one_outcome(self) -> RouteDecision:
        if self.unsupported == (self.skill is not None):
            raise ValueError("Specify one Skill or mark the request unsupported")
        return self


class ArgumentDecision(StrictModel):
    """Typed envelope used before arguments reach a Skill schema."""

    arguments: dict[str, Any]


class ChildSelectionDecision(StrictModel):
    skill: str


class ValidationOutcome(StrEnum):
    APPROVE = "APPROVE"
    EDIT = "EDIT"
    ASK_USER = "ASK_USER"
    REJECT = "REJECT"


class ValidationDecision(StrictModel):
    decision: ValidationOutcome
    reason: str | None = None
    revised_arguments: dict[str, Any] | None = None
    question: str | None = None

    @model_validator(mode="after")
    def require_decision_payload(self) -> ValidationDecision:
        if self.decision is ValidationOutcome.EDIT and self.revised_arguments is None:
            raise ValueError("EDIT requires revised_arguments")
        if self.decision is ValidationOutcome.ASK_USER and not self.question:
            raise ValueError("ASK_USER requires a question")
        return self


class InteractionRelationship(StrEnum):
    APPROVAL = "APPROVAL"
    REJECTION = "REJECTION"
    CANCELLATION = "CANCELLATION"
    EDIT = "EDIT"
    ANSWER = "ANSWER"
    NEW_REQUEST = "NEW_REQUEST"


class InteractionRelationshipDecision(StrictModel):
    relationship: InteractionRelationship


class ResultValidationOutcome(StrEnum):
    ACCEPT = "ACCEPT"
    RETRY = "RETRY"
    ASK_USER = "ASK_USER"
    FAIL = "FAIL"


class ResultValidationDecision(StrictModel):
    decision: ResultValidationOutcome
    reason: str | None = None
    question: str | None = None

    @model_validator(mode="after")
    def require_question_when_asking(self) -> ResultValidationDecision:
        if self.decision is ResultValidationOutcome.ASK_USER and not self.question:
            raise ValueError("ASK_USER requires a question")
        return self
