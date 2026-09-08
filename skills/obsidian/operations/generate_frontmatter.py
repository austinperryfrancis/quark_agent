"""Configurable, deterministic frontmatter generation and merging."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from skills.obsidian.models import NotePatch, ParsedNote
from skills.obsidian.operations.apply_changes import build_frontmatter_patch

DEFAULT_FIELDS = (
    "title",
    "type",
    "project",
    "created",
    "updated",
    "tags",
    "status",
    "action_required",
    "people",
)
NOTE_TYPES = (
    "meeting-note",
    "research-note",
    "idea",
    "reference",
    "project",
    "journal",
    "task-note",
)


@dataclass(frozen=True, slots=True)
class FrontmatterConfig:
    field_order: tuple[str, ...] = DEFAULT_FIELDS
    preserve_unknown_fields: bool = True
    overwrite_nonempty: bool = False
    max_tags: int = 8
    required_by_type: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def required(self, note_type: str | None) -> tuple[str, ...]:
        return self.required_by_type.get(note_type or "", ())


def _validate_value(field: str, value: Any, max_tags: int) -> None:
    if field in {"created", "updated"} and value is not None:
        date.fromisoformat(str(value))
    if field == "tags" and (
        not isinstance(value, list)
        or len(value) > max_tags
        or len(set(value)) != len(value)
    ):
        raise ValueError("tags must be a unique list within max_tags")
    if field == "action_required" and not isinstance(value, bool):
        raise ValueError("action_required must be boolean")


def generate_frontmatter(
    parsed: ParsedNote,
    semantic_fields: dict[str, Any] | None = None,
    *,
    deterministic_fields: dict[str, Any] | None = None,
    config: FrontmatterConfig | None = None,
) -> NotePatch:
    """Merge generated fields while preserving user-authored values by default."""
    settings = config or FrontmatterConfig()
    proposed = {**(deterministic_fields or {}), **(semantic_fields or {})}
    existing = parsed.frontmatter.data
    updates: dict[str, Any] = {}
    for key, value in proposed.items():
        _validate_value(key, value, settings.max_tags)
        if (
            settings.overwrite_nonempty
            or key not in existing
            or existing[key] in (None, "", [])
        ):
            updates[key] = value
    result = build_frontmatter_patch(
        parsed,
        updates,
        field_order=settings.field_order,
        preserve_unknown_fields=settings.preserve_unknown_fields,
    )
    missing = [
        key
        for key in settings.required(result.frontmatter.get("type"))
        if not result.frontmatter.get(key)
    ]
    if missing:
        raise ValueError(f"required frontmatter fields missing: {', '.join(missing)}")
    return result
