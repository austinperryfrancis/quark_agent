"""Build, preview, and atomically apply structured note patches."""

from __future__ import annotations

import os
import tempfile
import time
from collections.abc import Iterable, Mapping
from difflib import unified_diff
from hashlib import sha256
from pathlib import Path
from stat import S_IMODE
from typing import Any

import yaml

from skills.obsidian.errors import (
    InvalidNotePatchError,
    NoteChangedBeforeWriteError,
    NoteReadError,
)
from skills.obsidian.models import ApplyResult, FrontmatterStatus, NotePatch, ParsedNote
from skills.obsidian.operations.parse_frontmatter import parse_note
from skills.obsidian.operations.read_note import read_note
from skills.obsidian.operations.validate_note import validate_note_patch


def _ordered_fields(
    fields: Mapping[str, Any], field_order: Iterable[str]
) -> dict[str, Any]:
    order = tuple(field_order)
    keys = (*order, *(key for key in fields if key not in order))
    return {key: fields[key] for key in keys if key in fields}


def _serialize_frontmatter(fields: Mapping[str, Any]) -> bytes:
    rendered = yaml.safe_dump(
        dict(fields), allow_unicode=True, default_flow_style=False, sort_keys=False
    ).encode("utf-8")
    if yaml.safe_load(rendered) != dict(fields):
        raise InvalidNotePatchError("serialized frontmatter did not round-trip")
    return b"---\n" + rendered + b"---\n"


def build_frontmatter_patch(
    parsed: ParsedNote,
    updates: Mapping[str, Any],
    *,
    remove_fields: Iterable[str] = (),
    field_order: Iterable[str] = (),
    preserve_unknown_fields: bool = True,
) -> NotePatch:
    """Build a structured patch; the note body never comes from the model."""
    if parsed.frontmatter.status is FrontmatterStatus.MALFORMED:
        raise InvalidNotePatchError("cannot patch malformed frontmatter")

    existing = parsed.frontmatter.data if preserve_unknown_fields else {}
    fields = dict(existing)
    for key in remove_fields:
        fields.pop(key, None)
    fields.update(updates)
    ordered = _ordered_fields(fields, field_order)
    changed = {
        key: value for key, value in updates.items() if existing.get(key) != value
    }
    removed = tuple(key for key in remove_fields if key in existing)
    if not changed and not removed and preserve_unknown_fields:
        proposed = parsed.document.original_bytes
    else:
        proposed = _serialize_frontmatter(ordered) + parsed.frontmatter.body_bytes
    name = parsed.document.relative_path.as_posix()
    diff = "".join(
        unified_diff(
            parsed.document.content.splitlines(keepends=True),
            proposed.decode("utf-8").splitlines(keepends=True),
            fromfile=f"a/{name}",
            tofile=f"b/{name}",
        )
    )
    patch = NotePatch(
        note_id=parsed.document.note_id,
        path=parsed.document.path,
        original_hash=parsed.document.content_hash,
        original_bytes=parsed.document.original_bytes,
        original_body_bytes=parsed.frontmatter.body_bytes,
        frontmatter=ordered,
        changed_fields=changed,
        removed_fields=removed,
        proposed_bytes=proposed,
        proposed_hash=sha256(proposed).hexdigest(),
        diff=diff,
    )
    validation = validate_note_patch(patch)
    if not validation.valid:
        raise InvalidNotePatchError("; ".join(validation.errors))
    return patch


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".quark-tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _create_backup(vault_path: Path, patch: NotePatch, retention: int) -> Path:
    if retention < 1:
        raise ValueError("backup retention must be at least one")
    relative = patch.path.resolve().relative_to(vault_path.resolve())
    directory = vault_path / ".quark" / "backups" / relative.parent
    directory.mkdir(parents=True, exist_ok=True)
    backup = directory / (
        f"{relative.name}.{time.time_ns()}.{patch.original_hash[:12]}.bak"
    )
    _atomic_write(backup, patch.original_bytes, S_IMODE(patch.path.stat().st_mode))
    existing = sorted(
        directory.glob(f"{relative.name}.*.bak"),
        key=lambda item: item.stat().st_mtime_ns,
        reverse=True,
    )
    for expired in existing[retention:]:
        expired.unlink()
    return backup


def apply_changes(
    vault_path: Path,
    patch: NotePatch,
    *,
    dry_run: bool = True,
    create_backup: bool = True,
    backup_retention: int = 5,
) -> ApplyResult:
    """Validate and preview or atomically apply one note patch."""
    validation = validate_note_patch(patch)
    if not validation.valid:
        raise InvalidNotePatchError("; ".join(validation.errors))

    try:
        current = patch.path.read_bytes()
    except OSError as error:
        raise NoteReadError(f"could not re-read note before write: {error}") from error
    if sha256(current).hexdigest() != patch.original_hash:
        raise NoteChangedBeforeWriteError(
            f"note changed after patch creation: {patch.path}"
        )

    if dry_run:
        return ApplyResult(
            note_id=patch.note_id,
            applied=False,
            dry_run=True,
            original_hash=patch.original_hash,
            resulting_hash=patch.original_hash,
            diff=patch.diff,
            backup_path=None,
        )

    mode = S_IMODE(patch.path.stat().st_mode)
    before = parse_note(read_note(vault_path, patch.path), vault_path)
    backup = (
        _create_backup(vault_path, patch, backup_retention) if create_backup else None
    )
    try:
        _atomic_write(patch.path, patch.proposed_bytes, mode)
        result = read_note(vault_path, patch.path)
        parsed_result = parse_note(result, vault_path)
        if result.content_hash != patch.proposed_hash:
            raise NoteReadError("written note hash does not match the proposed hash")
        if parsed_result.frontmatter.body_bytes != patch.original_body_bytes:
            raise NoteReadError("written note does not preserve the original body")
        if parsed_result.frontmatter.data != patch.frontmatter:
            raise NoteReadError("written frontmatter does not match structured fields")
        if parsed_result.links != before.links:
            raise NoteReadError("written note links differ from original links")
    except Exception as error:
        try:
            after_failure = patch.path.read_bytes()
        except OSError:
            after_failure = b""
        if sha256(after_failure).hexdigest() == patch.original_hash:
            raise
        try:
            _atomic_write(patch.path, patch.original_bytes, mode)
        except Exception as rollback_error:
            raise NoteReadError(
                f"write verification failed and rollback failed: {rollback_error}"
            ) from error
        raise

    return ApplyResult(
        note_id=patch.note_id,
        applied=True,
        dry_run=False,
        original_hash=patch.original_hash,
        resulting_hash=patch.proposed_hash,
        diff=patch.diff,
        backup_path=backup,
    )
