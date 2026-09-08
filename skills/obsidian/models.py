"""Data returned by deterministic Obsidian operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class NoteReference:
    """A vault-relative note identity and its current filesystem location."""

    note_id: str
    relative_path: Path
    path: Path


@dataclass(frozen=True, slots=True)
class NoteDocument:
    """An immutable note snapshot captured by one safe filesystem read."""

    note_id: str
    relative_path: Path
    path: Path
    modified_at: datetime
    modified_ns: int
    size: int
    content_hash: str
    original_bytes: bytes
    content: str
    analysis_content: str


class FrontmatterStatus(StrEnum):
    ABSENT = "absent"
    EMPTY = "empty"
    VALID = "valid"
    MALFORMED = "malformed"


@dataclass(frozen=True, slots=True)
class FrontmatterDocument:
    """Parsed frontmatter plus exact source segments for lossless updates."""

    status: FrontmatterStatus
    data: dict[str, Any]
    original_bytes: bytes
    frontmatter_bytes: bytes | None
    body_bytes: bytes
    opening_end: int | None
    closing_start: int | None
    closing_end: int | None
    error: str | None = None


class LinkStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    BROKEN = "broken"


@dataclass(frozen=True, slots=True)
class WikiLink:
    """A parsed wiki-link and its deterministic vault resolution."""

    raw: str
    target: str
    heading: str | None
    alias: str | None
    embedded: bool
    status: LinkStatus
    resolved_note_id: str | None
    candidates: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedNote:
    """Deterministically parsed structures from one immutable note snapshot."""

    document: NoteDocument
    frontmatter: FrontmatterDocument
    yaml_tags: tuple[str, ...]
    inline_tags: tuple[str, ...]
    links: tuple[WikiLink, ...]


@dataclass(frozen=True, slots=True)
class NotePatch:
    """A structured frontmatter change built from a specific note snapshot."""

    note_id: str
    path: Path
    original_hash: str
    original_bytes: bytes
    original_body_bytes: bytes
    frontmatter: dict[str, Any]
    changed_fields: dict[str, Any]
    removed_fields: tuple[str, ...]
    proposed_bytes: bytes
    proposed_hash: str
    diff: str


@dataclass(frozen=True, slots=True)
class PatchValidation:
    """Deterministic validation result for a proposed note patch."""

    valid: bool
    errors: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Outcome of previewing or applying a validated patch."""

    note_id: str
    applied: bool
    dry_run: bool
    original_hash: str
    resulting_hash: str
    diff: str
    backup_path: Path | None
