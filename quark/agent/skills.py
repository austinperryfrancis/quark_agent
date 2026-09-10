"""Generic executable skill contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, cast

from quark.agent.lifecycle import (
    Proposal,
    ProposalOutcome,
)
from quark.agent.models import IntentDefinition
from quark.config import QuarkConfig
from quark.models.provider import ModelProvider
from quark.state.database import StateDatabase


@dataclass(frozen=True, slots=True)
class SkillContext:
    config: QuarkConfig
    state: StateDatabase
    vault: Path | None = None
    provider: ModelProvider | None = None


@dataclass(frozen=True, slots=True)
class SkillResult:
    text: str
    data: dict[str, Any] = field(default_factory=dict)
    proposed_action: dict[str, Any] | None = None
    proposal: Proposal | None = None


class ExecutableSkill(Protocol):
    name: str

    def intents(self) -> tuple[IntentDefinition, ...]: ...
    def propose(self, operation: str, arguments: dict[str, Any]) -> Proposal: ...
    def revise_proposal(self, proposal: Proposal, feedback: str) -> Proposal: ...
    def explain_proposal(self, proposal: Proposal, question: str) -> str: ...
    def apply_proposal(self, proposal: Proposal) -> ProposalOutcome: ...


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Any] = {}

    def register(self, skill: ExecutableSkill) -> None:
        if skill.name in self._skills:
            raise ValueError(f"duplicate executable skill: {skill.name}")
        required = ("propose", "revise_proposal", "explain_proposal", "apply_proposal")
        missing = [name for name in required if not callable(getattr(skill, name, None))]
        if missing:
            raise TypeError(f"skill {skill.name!r} missing lifecycle methods: {', '.join(missing)}")
        self._skills[skill.name] = skill

    def get(self, name: str) -> ExecutableSkill:
        try:
            return cast(ExecutableSkill, self._skills[name])
        except KeyError as error:
            raise ValueError(f"unknown executable skill: {name}") from error

    def intents(self) -> tuple[IntentDefinition, ...]:
        return tuple(
            intent for skill in self._skills.values() for intent in skill.intents()
        )

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._skills))
