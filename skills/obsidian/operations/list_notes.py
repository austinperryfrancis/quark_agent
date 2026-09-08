"""Discover Markdown notes without requiring model reasoning."""

from __future__ import annotations

import os
from collections.abc import Iterable
from fnmatch import fnmatch
from pathlib import Path

from skills.obsidian.errors import InvalidVaultError, VaultNotFoundError
from skills.obsidian.models import NoteReference

DEFAULT_EXCLUSIONS = (
    ".obsidian",
    ".quark",
    "Templates",
    "Archive",
    "backups",
    "indexes",
)


def _vault_root(vault_path: Path) -> Path:
    try:
        root = vault_path.expanduser().resolve(strict=True)
    except FileNotFoundError as error:
        raise VaultNotFoundError(
            f"Obsidian vault does not exist: {vault_path}"
        ) from error
    if not root.is_dir():
        raise InvalidVaultError(f"Obsidian vault is not a directory: {vault_path}")
    return root


def _is_excluded(relative_path: Path, exclusions: tuple[str, ...]) -> bool:
    relative = relative_path.as_posix()
    return any(
        fnmatch(relative, pattern)
        or fnmatch(relative, f"{pattern}/*")
        or any(fnmatch(part, pattern) for part in relative_path.parts)
        for pattern in exclusions
    )


def note_id_for(relative_path: Path) -> str:
    """Return an identity stable across copies or moves of the entire vault."""
    return relative_path.as_posix()


def list_notes(
    vault_path: Path,
    exclusions: Iterable[str] = DEFAULT_EXCLUSIONS,
) -> list[NoteReference]:
    """Recursively list regular, non-symlinked Markdown files in a vault."""
    root = _vault_root(vault_path)
    excluded = tuple(exclusions)
    notes: list[NoteReference] = []

    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        directories[:] = sorted(
            directory
            for directory in directories
            if not (current_path / directory).is_symlink()
            and not _is_excluded((current_path / directory).relative_to(root), excluded)
        )
        for filename in sorted(files):
            path = current_path / filename
            relative_path = path.relative_to(root)
            if (
                path.is_symlink()
                or not path.is_file()
                or path.suffix.lower() != ".md"
                or _is_excluded(relative_path, excluded)
            ):
                continue
            notes.append(
                NoteReference(
                    note_id=note_id_for(relative_path),
                    relative_path=relative_path,
                    path=path,
                )
            )

    return sorted(notes, key=lambda note: note.note_id.casefold())
