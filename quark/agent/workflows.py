"""Contracts for multi-step workflows composed from executable skills."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from quark.agent.lifecycle import Proposal


class WorkflowStage(StrEnum):
    GENERATE_FRONTMATTER = "generate_frontmatter"
    GENERATE_TAGS = "generate_tags"
    ADD_WIKILINKS = "add_wikilinks"
    MOVE_NOTE = "move_note"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True)
class WorkflowResult:
    """Preview or completion returned by a workflow."""

    text: str
    data: dict[str, Any] = field(default_factory=dict)
    proposed_action: dict[str, Any] | None = None


class Workflow(Protocol):
    """A named, preview-first procedure composed from skills."""

    name: str
    description: str

    def execute(self, arguments: dict[str, Any]) -> WorkflowResult: ...

    def apply(self, payload: dict[str, Any]) -> WorkflowResult: ...


class StagedWorkflow(Protocol):
    """A workflow whose only responsibility is sequencing skill proposals."""

    name: str

    def start(self, arguments: dict[str, Any]) -> Proposal: ...
    def advance(
        self, proposal: Proposal, applied: dict[str, Any]
    ) -> Proposal | None: ...


class WorkflowRegistry:
    """Registry kept separate from atomic skill registration."""

    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}

    def register(self, workflow: Workflow) -> None:
        if workflow.name in self._workflows:
            raise ValueError(f"duplicate workflow: {workflow.name}")
        self._workflows[workflow.name] = workflow

    def get(self, name: str) -> Workflow:
        return self._workflows[name]

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._workflows))
