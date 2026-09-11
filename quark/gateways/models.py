"""Typed messages exchanged by gateways and the Quark runtime."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class GatewayCommand(StrEnum):
    CHAT = "CHAT"
    STATUS = "STATUS"
    SKILLS = "SKILLS"
    RUNS = "RUNS"
    RUN = "RUN"
    RECOVER = "RECOVER"


class RecoveryAction(StrEnum):
    RETRY = "RETRY"
    CANCEL = "CANCEL"


class GatewayRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: GatewayCommand
    session_key: str | None = None
    text: str | None = None
    run_id: str | None = None
    skill_call_id: str | None = None
    recovery_action: RecoveryAction | None = None

    @model_validator(mode="after")
    def validate_chat_fields(self) -> GatewayRequest:
        if self.command is GatewayCommand.CHAT:
            if not self.session_key or not self.text or not self.text.strip():
                raise ValueError("CHAT requires session_key and non-empty text")
        if self.command in {GatewayCommand.RUN, GatewayCommand.RECOVER}:
            if not self.run_id:
                raise ValueError(f"{self.command.value} requires run_id")
        if self.command is GatewayCommand.RECOVER and self.recovery_action is None:
            raise ValueError("RECOVER requires recovery_action")
        return self


class GatewayResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ok: bool
    kind: str = Field(min_length=1)
    message: str
    run_id: str | None = None
    skill_call_id: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
