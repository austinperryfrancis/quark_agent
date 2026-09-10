"""Skill-independent conversation and routing models."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class RouteKind(StrEnum):
    CHAT = "chat"
    SKILL_QUERY = "skill_query"
    START_PROCESS = "start_process"
    CONTINUE_PROCESS = "continue_process"
    CLARIFY = "clarify"
    COMMAND = "command"


@dataclass(frozen=True, slots=True)
class IntentDefinition:
    name: str
    skill: str
    description: str
    examples: tuple[str, ...] = ()
    process: bool = False
    write: bool = False
    keywords: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteDecision:
    route: RouteKind
    confidence: float
    skill: str | None = None
    intent: str | None = None
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


@dataclass(frozen=True, slots=True)
class AgentResponse:
    text: str
    route: RouteDecision
    pending_confirmation: bool = False
    data: dict[str, Any] = field(default_factory=dict)
