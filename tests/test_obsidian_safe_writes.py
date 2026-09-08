from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from skills.obsidian.errors import (
    InvalidNotePatchError,
    NoteChangedBeforeWriteError,
    NoteReadError,
)
from skills.obsidian.models import FrontmatterStatus, ParsedNote
from skills.obsidian.operations import apply_changes as apply_module
from skills.obsidian.operations.apply_changes import (
    apply_changes,
    build_frontmatter_patch,
)
from skills.obsidian.operations.parse_frontmatter import parse_note
from skills.obsidian.operations.read_note import read_note
from skills.obsidian.operations.validate_note import validate_note_patch


def _parsed(vault_path: Path, relative: str = "Inbox/Meeting Note.md") -> ParsedNote:
    return parse_note(read_note(vault_path, relative), vault_path)


def test_structured_patch_orders_fields_and_round_trips_yaml(vault_path: Path) -> None:
    parsed = _parsed(vault_path)
    patch = build_frontmatter_patch(
        parsed,
        {"status": "active", "action_required": True},
        field_order=["title", "type", "project", "status", "tags"],
    )

    assert list(patch.frontmatter) == [
        "title",
        "type",
        "project",
        "status",
        "tags",
        "action_required",
    ]
    assert patch.changed_fields == {"status": "active", "action_required": True}
    assert validate_note_patch(patch).valid
    assert patch.proposed_bytes.endswith(parsed.frontmatter.body_bytes)
    rendered = patch.proposed_bytes.split(b"---\n", 2)[1]
    assert yaml.safe_load(rendered) == patch.frontmatter
    assert "--- a/Inbox/Meeting Note.md" in patch.diff
    assert "+status: active" in patch.diff


def test_patch_preserves_unknown_fields_and_can_remove_explicit_fields(
    vault_path: Path,
) -> None:
    parsed = _parsed(vault_path, "Inbox/Partial Frontmatter.md")
    patch = build_frontmatter_patch(
        parsed,
        {"type": "research-note"},
        remove_fields=["title"],
    )

    assert patch.frontmatter["custom_user_field"] == "preserve-me"
    assert "title" not in patch.frontmatter
    assert patch.removed_fields == ("title",)


def test_dry_run_returns_diff_without_changing_note(vault_path: Path) -> None:
    parsed = _parsed(vault_path)
    patch = build_frontmatter_patch(parsed, {"status": "active"})
    before = patch.path.read_bytes()

    result = apply_changes(vault_path, patch, dry_run=True)

    assert not result.applied
    assert result.dry_run
    assert result.diff == patch.diff
    assert result.backup_path is None
    assert patch.path.read_bytes() == before


def test_no_op_round_trip_leaves_note_byte_identical(vault_path: Path) -> None:
    parsed = _parsed(vault_path)
    patch = build_frontmatter_patch(parsed, {})

    assert patch.changed_fields == {}
    assert patch.removed_fields == ()
    assert patch.proposed_bytes == parsed.document.original_bytes
    assert patch.diff == ""

    result = apply_changes(vault_path, patch, dry_run=False, create_backup=False)
    assert result.applied
    assert patch.path.read_bytes() == parsed.document.original_bytes


def test_apply_atomically_reopens_verifies_and_creates_backup(vault_path: Path) -> None:
    parsed = _parsed(vault_path)
    patch = build_frontmatter_patch(parsed, {"status": "active"})

    result = apply_changes(vault_path, patch, dry_run=False)
    updated = _parsed(vault_path)

    assert result.applied
    assert result.resulting_hash == patch.proposed_hash
    assert updated.frontmatter.status is FrontmatterStatus.VALID
    assert updated.frontmatter.data["status"] == "active"
    assert updated.frontmatter.body_bytes == parsed.frontmatter.body_bytes
    assert updated.links == parsed.links
    assert result.backup_path is not None
    assert result.backup_path.read_bytes() == patch.original_bytes


def test_concurrent_change_prevents_write(vault_path: Path) -> None:
    parsed = _parsed(vault_path)
    patch = build_frontmatter_patch(parsed, {"status": "active"})
    changed = patch.original_bytes + b"\nUser edit after read.\n"
    patch.path.write_bytes(changed)

    with pytest.raises(NoteChangedBeforeWriteError, match="changed after patch"):
        apply_changes(vault_path, patch, dry_run=False)
    assert patch.path.read_bytes() == changed


def test_atomic_replace_failure_leaves_original_intact(
    vault_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = _parsed(vault_path)
    patch = build_frontmatter_patch(parsed, {"status": "active"})

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError(f"simulated replacement failure: {source} -> {destination}")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated replacement failure"):
        apply_changes(vault_path, patch, dry_run=False, create_backup=False)

    assert patch.path.read_bytes() == patch.original_bytes
    assert not list(patch.path.parent.glob(".*.quark-tmp"))


def test_backup_retention_is_per_note(vault_path: Path) -> None:
    for revision in range(3):
        parsed = _parsed(vault_path)
        patch = build_frontmatter_patch(parsed, {"revision": revision})
        apply_changes(
            vault_path,
            patch,
            dry_run=False,
            create_backup=True,
            backup_retention=2,
        )

    backups = list(
        (vault_path / ".quark" / "backups" / "Inbox").glob("Meeting Note.md.*.bak")
    )
    assert len(backups) == 2


def test_malformed_frontmatter_cannot_be_patched(vault_path: Path) -> None:
    parsed = _parsed(vault_path, "Inbox/Malformed Frontmatter.md")
    with pytest.raises(InvalidNotePatchError, match="malformed frontmatter"):
        build_frontmatter_patch(parsed, {"status": "active"})


def test_verification_failure_rolls_back_original(
    vault_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    parsed = _parsed(vault_path)
    patch = build_frontmatter_patch(parsed, {"status": "active"})
    real_atomic_write = apply_module._atomic_write
    calls = 0

    def corrupt_first_write(path: Path, content: bytes, mode: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            real_atomic_write(path, content + b"corruption", mode)
        else:
            real_atomic_write(path, content, mode)

    monkeypatch.setattr(apply_module, "_atomic_write", corrupt_first_write)
    with pytest.raises(NoteReadError, match="hash does not match"):
        apply_changes(vault_path, patch, dry_run=False, create_backup=False)

    assert patch.path.read_bytes() == patch.original_bytes
