"""Minimal per-operation semantic context construction."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class SemanticTask:
    operation: str
    role: str
    facts: dict[str, Any] = field(default_factory=dict)
    choices: tuple[str, ...] = ()
    output_schema: dict[str, Any] | None = None
    content_hash: str = ""
    model_policy: dict[str, Any] = field(default_factory=dict)
    max_input_tokens: int = 500


def compile_context(task: SemanticTask, role_instructions: str = "") -> str:
    """Build a compact prompt from only facts, choices, and the output schema."""
    budget = max(128, task.max_input_tokens * 4)
    sections = [f"TASK: {task.operation}", f"ROLE: {task.role}"]
    if role_instructions.strip():
        sections.append(f"INSTRUCTIONS:\n{role_instructions.strip()}")
    if task.choices:
        sections.append(
            "CHOICES:\n" + "\n".join(f"- {choice}" for choice in task.choices)
        )
    if task.facts:
        fact_lines = []
        for key, value in task.facts.items():
            rendered = str(value)
            remaining = max(80, budget // max(1, len(task.facts)))
            fact_lines.append(f"{key}: {rendered[:remaining]}")
        sections.append("FACTS:\n" + "\n".join(fact_lines))
    if task.output_schema is not None:
        sections.append(f"OUTPUT JSON SCHEMA:\n{task.output_schema}")
    sections.append("OUTPUT: Return JSON only.")
    prompt = "\n\n".join(sections)
    return prompt[:budget]
