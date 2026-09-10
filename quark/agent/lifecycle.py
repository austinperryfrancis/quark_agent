"""Shared, durable proposal lifecycle for Quark skills and workflow stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256
from json import JSONDecodeError, dumps, loads
from typing import Any, Protocol
from uuid import uuid4


class ValidationMode(StrEnum):
    USER = "user"
    AGENT = "agent"
    OFF = "off"


class FeedbackKind(StrEnum):
    APPROVE = "approve"
    CANCEL = "cancel"
    SHOW = "show"
    EXPLAIN = "explain"
    REVISE = "revise"


@dataclass(frozen=True, slots=True)
class Validation:
    valid: bool
    reason: str = ""
    confidence: float | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Proposal:
    skill: str
    operation: str
    payload: dict[str, Any]
    deterministic: Validation
    semantic: Validation
    mode: ValidationMode = ValidationMode.USER
    revision: int = 0
    text: str = ""
    request: str = ""
    proposal_id: str = field(default_factory=lambda: uuid4().hex)
    input_digest: str = ""


@dataclass(frozen=True, slots=True)
class ProposalOutcome:
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    next_proposal: Proposal | None = None


class LifecycleSkill(Protocol):
    """A bounded operation with a uniform proposal and review lifecycle."""

    name: str

    def propose(self, operation: str, arguments: dict[str, Any]) -> Proposal: ...
    def revise_proposal(self, proposal: Proposal, feedback: str) -> Proposal: ...
    def explain_proposal(self, proposal: Proposal, question: str) -> str: ...
    def apply_proposal(self, proposal: Proposal) -> ProposalOutcome: ...


def payload_digest(value: Any) -> str:
    return sha256(dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def classify_feedback(message: str) -> FeedbackKind:
    value = message.strip().casefold()
    if value in {"yes", "y", "apply", "confirm", "approve"}:
        return FeedbackKind.APPROVE
    if value in {"no", "n", "cancel", "reject"}:
        return FeedbackKind.CANCEL
    if value in {"show", "show proposal", "show current proposal", "/proposal"}:
        return FeedbackKind.SHOW
    if value.startswith("why") or value.startswith("explain"):
        return FeedbackKind.EXPLAIN
    return FeedbackKind.REVISE


def review_required(proposal: Proposal) -> bool:
    """Safety failures always pause; `off` skips only routine human review."""
    if not proposal.deterministic.valid or not proposal.semantic.valid:
        return True
    return proposal.mode is ValidationMode.USER


def proposal_payload(proposal: Proposal) -> dict[str, Any]:
    """Serialize every field needed to resume or audit a proposal."""
    return {"_proposal": asdict(proposal), "payload": proposal.payload}


def proposal_from_payload(payload: dict[str, Any]) -> Proposal:
    """Restore a typed proposal and reject stale/incomplete envelopes safely."""
    try:
        raw = dict(payload["_proposal"])
        deterministic = Validation(**dict(raw.pop("deterministic")))
        semantic = Validation(**dict(raw.pop("semantic")))
        raw["mode"] = ValidationMode(raw["mode"])
        stored_payload = payload.get("payload")
        raw["payload"] = dict(
            stored_payload if stored_payload is not None else raw["payload"]
        )
        return Proposal(deterministic=deterministic, semantic=semantic, **raw)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            "pending proposal has an invalid lifecycle envelope"
        ) from error


def parse_json_object(value: str) -> dict[str, Any] | None:
    """Small-model JSON helper: only accept an unambiguous object."""
    try:
        parsed = loads(value)
    except JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
