"""Typed capability metadata and skill manifest models."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class RiskClass(StrEnum):
    PURE = "PURE"
    READ = "READ"
    WRITE = "WRITE"
    DESTRUCTIVE = "DESTRUCTIVE"
    EXTERNAL = "EXTERNAL"
    PRIVILEGED = "PRIVILEGED"


class ExecutionLane(StrEnum):
    DETERMINISTIC = "deterministic"
    SMALL_MODEL = "small_model"
    ENHANCED_MODEL = "enhanced_model"
    ESCALATION = "escalation"


class CapabilityMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str = ""
    domain: str = "core"
    operation_type: str = "deterministic"
    lane: ExecutionLane = ExecutionLane.DETERMINISTIC
    risk: RiskClass = RiskClass.PURE
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    requires: list[str] = Field(default_factory=list)
    model_required: bool = False
    timeout_seconds: float = Field(default=60.0, gt=0)
    max_attempts: int = Field(default=3, ge=1, le=10)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    entrypoint: str | None = None
    intents: list[dict[str, Any]] = Field(default_factory=list)
    capabilities: list[str]
    capability_metadata: dict[str, dict[str, Any]] = Field(default_factory=dict)
    permissions: dict[str, Any] = Field(default_factory=dict)
