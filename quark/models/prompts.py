"""Specialized semantic prompt roles."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path


class PromptRole(StrEnum):
    TAGGER = "tagger"
    CLASSIFIER = "note_classifier"
    METADATA_EXTRACTOR = "metadata_extractor"
    RELATIONSHIP_JUDGE = "relationship_checker"
    SUMMARIZER = "summarizer"
    VERIFIER = "verifier"
    REPAIRER = "repairer"


def load_prompt(
    role: PromptRole | str, root: Path = Path("skills/obsidian/prompts")
) -> str:
    name = role.value if isinstance(role, PromptRole) else role
    path = root / f"{name}.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise FileNotFoundError(f"prompt role is unavailable: {name}") from error
