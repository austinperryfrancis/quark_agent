"""Persistent canonical tag taxonomy and inexpensive candidate retrieval."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path

from quark.state.database import StateDatabase
from skills.obsidian.operations.list_notes import DEFAULT_EXCLUSIONS, list_notes
from skills.obsidian.operations.parse_frontmatter import (
    extract_inline_tags,
    extract_yaml_tags,
    parse_frontmatter,
)
from skills.obsidian.operations.read_note import read_note

_SEPARATORS = re.compile(r"[\s_]+")
_INVALID = re.compile(r"[^\w/-]+", re.UNICODE)


def normalize_tag(tag: str) -> str:
    """Normalize equivalent tag spellings to a stable comparison key."""
    value = tag.strip().removeprefix("#").casefold()
    value = _SEPARATORS.sub("-", value)
    return _INVALID.sub("", value).strip("-/")


class TagPolicy:
    """Configurable safety rules for model-proposed tags."""

    def __init__(
        self,
        *,
        protected: Iterable[str] = (),
        forbidden: Iterable[str] = (),
        max_tags: int = 8,
    ) -> None:
        self.protected = {normalize_tag(tag) for tag in protected if normalize_tag(tag)}
        self.forbidden = {normalize_tag(tag) for tag in forbidden if normalize_tag(tag)}
        self.max_tags = max_tags

    def validate(self, tags: Iterable[str]) -> tuple[str, ...]:
        normalized: list[str] = []
        for tag in tags:
            value = normalize_tag(tag)
            if value and value not in normalized:
                normalized.append(value)
        if len(normalized) > self.max_tags:
            raise ValueError("tag policy max_tags exceeded")
        forbidden = self.forbidden.intersection(normalized)
        if forbidden:
            raise ValueError(f"forbidden tags proposed: {sorted(forbidden)}")
        missing = self.protected.difference(normalized)
        if missing:
            raise ValueError(f"protected tags missing: {sorted(missing)}")
        return tuple(normalized)


class TagTaxonomy:
    def __init__(
        self, state: StateDatabase, aliases: dict[str, str] | None = None
    ) -> None:
        self.state = state
        self.aliases = {
            normalize_tag(alias): normalize_tag(canonical)
            for alias, canonical in (aliases or {}).items()
        }

    def canonicalize(self, tag: str) -> str:
        normalized = normalize_tag(tag)
        normalized = self.aliases.get(normalized, normalized)
        for row in self.state.list_tag_index():
            canonical = str(row["canonical_tag"])
            aliases = json.loads(row["aliases_json"])
            if normalized == canonical or normalized in {
                normalize_tag(alias) for alias in aliases
            }:
                return canonical
        return normalized

    def observe(self, tag: str) -> str:
        alias = tag.strip().removeprefix("#")
        canonical = self.canonicalize(alias)
        self.state.replace_tag_observations(
            "__taxonomy__", "taxonomy", [(canonical, alias)]
        )
        return canonical

    def candidates(
        self, text: str, limit: int = 15, *, project: str | None = None
    ) -> list[str]:
        """Return frequent tags ranked by note-text and optional project overlap."""
        context = f"{text} {project or ''}"
        words = set(re.findall(r"[\w/-]+", context.casefold()))
        scored: list[tuple[int, int, str]] = []
        for row in self.state.list_tag_index():
            canonical = str(row["canonical_tag"])
            aliases = json.loads(row["aliases_json"])
            terms = {canonical, *(normalize_tag(alias) for alias in aliases)}
            overlap = sum(1 for term in terms if term and term in words)
            scored.append((-overlap, -int(row["frequency"]), canonical))
        return [tag for _, _, tag in sorted(scored)[:limit]]

    def sync_vault(
        self, vault_path: Path, exclusions: Iterable[str] = DEFAULT_EXCLUSIONS
    ) -> int:
        """Rebuild observations deterministically from the current vault snapshot."""
        self.state.clear_tag_observations()
        count = 0
        for reference in list_notes(vault_path, exclusions):
            document = read_note(vault_path, reference)
            frontmatter = parse_frontmatter(document)
            raw_tags = [
                *extract_yaml_tags(frontmatter),
                *extract_inline_tags(document.analysis_content),
            ]
            pairs = []
            seen: set[str] = set()
            for raw in raw_tags:
                canonical = self.canonicalize(raw)
                if canonical and canonical not in seen:
                    pairs.append((canonical, raw))
                    seen.add(canonical)
            self.state.replace_tag_observations(
                document.note_id, document.content_hash, pairs
            )
            count += len(pairs)
        return count
