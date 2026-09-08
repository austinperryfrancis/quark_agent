"""Safely read an immutable snapshot of an Obsidian note."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from skills.obsidian.errors import (
    InvalidVaultError,
    NoteChangedDuringReadError,
    NoteEncodingError,
    NoteNotFoundError,
    NoteOutsideVaultError,
    NoteReadError,
    VaultNotFoundError,
)
from skills.obsidian.models import NoteDocument, NoteReference
from skills.obsidian.operations.list_notes import note_id_for


def _resolve_root(vault_path: Path) -> Path:
    try:
        root = vault_path.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise VaultNotFoundError(
            f"Obsidian vault does not exist: {vault_path}"
        ) from error
    if not root.is_dir():
        raise InvalidVaultError(f"Obsidian vault is not a directory: {vault_path}")
    return root


def _resolve_note(root: Path, note: NoteReference | Path | str) -> tuple[Path, Path]:
    candidate = note.path if isinstance(note, NoteReference) else Path(note)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        path = candidate.resolve(strict=True)
    except FileNotFoundError as error:
        raise NoteNotFoundError(f"Obsidian note does not exist: {candidate}") from error
    try:
        relative_path = path.relative_to(root)
    except ValueError as error:
        raise NoteOutsideVaultError(
            f"Note is outside the configured vault: {path}"
        ) from error
    if not path.is_file():
        raise NoteReadError(f"Obsidian note is not a regular file: {path}")
    if path.suffix.lower() != ".md":
        raise NoteReadError(f"Obsidian note must be a Markdown file: {path}")
    return path, relative_path


def _signature(path: Path) -> tuple[int, int, int]:
    stat = path.stat()
    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def read_note(vault_path: Path, note: NoteReference | Path | str) -> NoteDocument:
    """Read UTF-8 content and metadata while detecting concurrent modification."""
    root = _resolve_root(vault_path)
    path, relative_path = _resolve_note(root, note)

    try:
        before = _signature(path)
        original_bytes = path.read_bytes()
        after = _signature(path)
    except OSError as error:
        raise NoteReadError(f"Could not read Obsidian note {path}: {error}") from error
    if before != after or len(original_bytes) != after[1]:
        raise NoteChangedDuringReadError(
            f"Obsidian note changed while being read: {path}"
        )

    try:
        content = original_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise NoteEncodingError(f"Obsidian note is not valid UTF-8: {path}") from error

    modified_ns, size, _ = after
    return NoteDocument(
        note_id=note_id_for(relative_path),
        relative_path=relative_path,
        path=path,
        modified_at=datetime.fromtimestamp(modified_ns / 1_000_000_000, tz=UTC),
        modified_ns=modified_ns,
        size=size,
        content_hash=sha256(original_bytes).hexdigest(),
        original_bytes=original_bytes,
        content=content,
        analysis_content=content.replace("\r\n", "\n").replace("\r", "\n"),
    )
