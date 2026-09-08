"""Compile known procedures into validated Quark intermediate representation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from quark.capabilities.registry import CapabilityError, CapabilityRegistry


class IRValidationError(ValueError):
    """A Quark IR graph is malformed or unsafe to execute."""


@dataclass(frozen=True, slots=True)
class IRStep:
    id: str
    operation: str
    depends_on: tuple[str, ...] = ()
    inputs: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class QuarkIR:
    goal_type: str
    target: str | None
    steps: tuple[IRStep, ...]

    def validate(self, registry: CapabilityRegistry | None = None) -> None:
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise IRValidationError("step identifiers must be unique")
        known = set(ids)
        for step in self.steps:
            missing = set(step.depends_on) - known
            if missing:
                raise IRValidationError(
                    f"step {step.id} depends on missing steps: {sorted(missing)}"
                )
            if registry is not None:
                try:
                    registry.get(step.operation)
                except CapabilityError as error:
                    raise IRValidationError(str(error)) from error
        visiting: set[str] = set()
        visited: set[str] = set()
        by_id = {step.id: step for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise IRValidationError(f"cycle detected at step: {step_id}")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in by_id[step_id].depends_on:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)


def compile_procedure(
    procedure_path: Path,
    *,
    target: str | None = None,
    registry: CapabilityRegistry | None = None,
) -> QuarkIR:
    try:
        raw = yaml.safe_load(procedure_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        raise IRValidationError(
            f"could not load procedure {procedure_path}: {error}"
        ) from error
    if not isinstance(raw, dict) or not isinstance(raw.get("procedure"), str):
        raise IRValidationError("procedure must contain a string procedure name")
    raw_steps = raw.get("steps")
    if not isinstance(raw_steps, list):
        raise IRValidationError("procedure steps must be a list")
    steps: list[IRStep] = []
    for raw_step in raw_steps:
        if not isinstance(raw_step, dict) or not isinstance(raw_step.get("id"), str):
            raise IRValidationError("each procedure step needs a string id")
        operation = raw_step.get("op")
        if not isinstance(operation, str):
            raise IRValidationError(f"step {raw_step['id']} needs a string op")
        dependencies = raw_step.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(item, str) for item in dependencies
        ):
            raise IRValidationError(
                f"step {raw_step['id']} depends_on must be a list of strings"
            )
        inputs = raw_step.get("inputs", {})
        if not isinstance(inputs, dict):
            raise IRValidationError(f"step {raw_step['id']} inputs must be a mapping")
        steps.append(IRStep(raw_step["id"], operation, tuple(dependencies), inputs))
    ir = QuarkIR(raw["procedure"], target, tuple(steps))
    ir.validate(registry)
    return ir


def compile_user_goal(
    request: str,
    *,
    procedure_directory: Path = Path("procedures"),
    registry: CapabilityRegistry | None = None,
) -> QuarkIR:
    """Compile only known MVP goal templates; arbitrary workflows are rejected."""
    normalized = request.strip().lower()
    if normalized.startswith("organize-inbox"):
        return compile_procedure(
            procedure_directory / "organize_note.yaml", registry=registry
        )
    if normalized.startswith("organize"):
        target = request.strip()[len("organize") :].strip() or None
        return compile_procedure(
            procedure_directory / "organize_note.yaml",
            target=target,
            registry=registry,
        )
    raise IRValidationError("unsupported goal; expected organize or organize-inbox")
