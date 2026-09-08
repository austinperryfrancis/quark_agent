from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from shutil import copytree

import pytest

from skills.obsidian.errors import (
    InvalidVaultError,
    NoteEncodingError,
    NoteNotFoundError,
    NoteOutsideVaultError,
    VaultNotFoundError,
)
from skills.obsidian.operations.list_notes import list_notes
from skills.obsidian.operations.read_note import read_note


def test_list_notes_recursively_returns_only_markdown(vault_path: Path) -> None:
    (vault_path / "Inbox" / "attachment.pdf").write_bytes(b"not a note")
    nested = vault_path / "Research" / "Nested"
    nested.mkdir()
    (nested / "Upper.MD").write_text("upper suffix", encoding="utf-8")

    notes = list_notes(vault_path, exclusions=[])
    note_ids = [note.note_id for note in notes]

    assert "Inbox/Meeting Note.md" in note_ids
    assert "Research/Nested/Upper.MD" in note_ids
    assert "Inbox/attachment.pdf" not in note_ids
    assert note_ids == sorted(note_ids, key=str.casefold)


def test_list_notes_prunes_configured_exclusions(vault_path: Path) -> None:
    excluded_paths = [
        vault_path / ".obsidian" / "Internal.md",
        vault_path / "Templates" / "Template.md",
        vault_path / "Archive" / "Old.md",
        vault_path / "backups" / "Backup.md",
        vault_path / "generated-output" / "Generated.md",
    ]
    for path in excluded_paths:
        path.parent.mkdir(exist_ok=True)
        path.write_text("excluded", encoding="utf-8")

    note_ids = {
        note.note_id
        for note in list_notes(
            vault_path,
            exclusions=[
                ".obsidian",
                "Templates",
                "Archive",
                "backups",
                "generated-*",
            ],
        )
    }

    assert not note_ids.intersection(
        path.relative_to(vault_path).as_posix() for path in excluded_paths
    )


def test_note_identifier_is_independent_of_vault_location(
    vault_path: Path, tmp_path: Path
) -> None:
    second_vault = tmp_path / "another-location"
    copytree(vault_path, second_vault)

    first_ids = [note.note_id for note in list_notes(vault_path)]
    second_ids = [note.note_id for note in list_notes(second_vault)]

    assert first_ids == second_ids
    assert all(not note_id.startswith(str(vault_path)) for note_id in first_ids)


def test_read_note_captures_exact_content_metadata_and_hash(vault_path: Path) -> None:
    reference = next(
        note
        for note in list_notes(vault_path)
        if note.note_id == "Inbox/Meeting Note.md"
    )
    document = read_note(vault_path, reference)

    assert document.note_id == "Inbox/Meeting Note.md"
    assert document.relative_path == Path("Inbox/Meeting Note.md")
    assert document.path == reference.path
    assert document.size == len(document.original_bytes)
    assert document.content_hash == sha256(document.original_bytes).hexdigest()
    assert document.content == document.original_bytes.decode("utf-8")
    assert document.modified_at.tzinfo is not None
    assert document.modified_ns > 0


def test_read_note_normalizes_only_analysis_content(vault_path: Path) -> None:
    path = vault_path / "Inbox" / "Windows Lines.md"
    source = b"first\r\nsecond\rthird\n"
    path.write_bytes(source)

    document = read_note(vault_path, Path("Inbox/Windows Lines.md"))

    assert document.original_bytes == source
    assert document.content == "first\r\nsecond\rthird\n"
    assert document.analysis_content == "first\nsecond\nthird\n"


def test_read_note_rejects_invalid_utf8(vault_path: Path) -> None:
    path = vault_path / "Inbox" / "Invalid Encoding.md"
    path.write_bytes(b"valid prefix\xffinvalid")

    with pytest.raises(NoteEncodingError, match="not valid UTF-8"):
        read_note(vault_path, path)


def test_discovery_and_read_errors_are_specific(
    vault_path: Path, tmp_path: Path
) -> None:
    with pytest.raises(VaultNotFoundError, match="does not exist"):
        list_notes(tmp_path / "missing-vault")

    regular_file = tmp_path / "not-a-vault"
    regular_file.write_text("file", encoding="utf-8")
    with pytest.raises(InvalidVaultError, match="not a directory"):
        list_notes(regular_file)

    with pytest.raises(NoteNotFoundError, match="does not exist"):
        read_note(vault_path, "Inbox/Missing.md")

    outside = tmp_path / "Outside.md"
    outside.write_text("outside", encoding="utf-8")
    with pytest.raises(NoteOutsideVaultError, match="outside"):
        read_note(vault_path, outside)
