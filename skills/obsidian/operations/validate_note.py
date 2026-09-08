"""Deterministically validate a proposed Obsidian note update."""

from __future__ import annotations

from hashlib import sha256
from typing import Any

import yaml

from skills.obsidian.models import NotePatch, PatchValidation


def _serialized_frontmatter(proposed: bytes) -> tuple[bytes | None, bytes | None]:
    lines = proposed.splitlines(keepends=True)
    if not lines or lines[0].rstrip(b"\r\n") != b"---":
        return None, None
    cursor = len(lines[0])
    for line in lines[1:]:
        if line.rstrip(b"\r\n") == b"---":
            return proposed[len(lines[0]) : cursor], proposed[cursor + len(line) :]
        cursor += len(line)
    return None, None


def validate_note_patch(patch: NotePatch) -> PatchValidation:
    """Validate hashes, YAML, structured fields, and exact body preservation."""
    errors: list[str] = []
    if sha256(patch.proposed_bytes).hexdigest() != patch.proposed_hash:
        errors.append("proposed content hash does not match proposed bytes")

    frontmatter_bytes, body_bytes = _serialized_frontmatter(patch.proposed_bytes)
    if frontmatter_bytes is None or body_bytes is None:
        errors.append("proposed note does not contain delimited frontmatter")
        return PatchValidation(valid=False, errors=tuple(errors))

    try:
        loaded: Any = yaml.safe_load(frontmatter_bytes.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as error:
        errors.append(f"proposed frontmatter does not parse safely: {error}")
    else:
        if not isinstance(loaded, dict):
            errors.append("proposed frontmatter root is not a mapping")
        elif loaded != patch.frontmatter:
            errors.append("serialized frontmatter differs from structured fields")

    if body_bytes != patch.original_body_bytes:
        errors.append("proposed update does not preserve the original note body")
    return PatchValidation(valid=not errors, errors=tuple(errors))
