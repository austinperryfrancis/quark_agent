"""Reusable deterministic proposal validation contracts."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True, slots=True)
class ValidationResult:
    valid: bool
    errors: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)


class ProposalValidator(Protocol):
    def validate(self, proposal: dict[str, Any]) -> ValidationResult: ...


class RequiredFieldsValidator:
    """Small generic validator for workflow stage payloads."""

    def __init__(self, *fields: str) -> None:
        self.fields = fields

    def validate(self, proposal: dict[str, Any]) -> ValidationResult:
        missing = tuple(field for field in self.fields if field not in proposal)
        return ValidationResult(
            not missing, tuple(f"missing field: {field}" for field in missing)
        )
