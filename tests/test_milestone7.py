from __future__ import annotations

from datetime import date

import pytest

from skills.obsidian.operations.generate_frontmatter import (
    FrontmatterConfig,
    generate_frontmatter,
)
from skills.obsidian.operations.parse_frontmatter import parse_note
from skills.obsidian.operations.read_note import read_note


def test_generation_preserves_unknown_and_nonempty_user_fields(vault_path) -> None:
    parsed = parse_note(read_note(vault_path, "Inbox/Meeting Note.md"), vault_path)
    patch = generate_frontmatter(
        parsed,
        {"status": "done", "title": "Generated title"},
        deterministic_fields={"updated": date.today().isoformat()},
    )
    assert patch.frontmatter["title"] == "Meeting with Advisor"
    assert patch.frontmatter["status"] == "done"
    assert patch.frontmatter["updated"] == date.today().isoformat()
    assert patch.frontmatter["project"] == "sanctions-paper"


def test_type_requirements_and_validation(vault_path) -> None:
    parsed = parse_note(read_note(vault_path, "Inbox/Meeting Note.md"), vault_path)
    config = FrontmatterConfig(required_by_type={"meeting-note": ("status",)})
    with pytest.raises(ValueError, match="required"):
        generate_frontmatter(parsed, {"type": "research-note"}, config=config)
    with pytest.raises(ValueError, match="unique"):
        generate_frontmatter(parsed, {"tags": ["a", "a"]})
