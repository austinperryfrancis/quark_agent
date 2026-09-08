from __future__ import annotations

from pathlib import Path

import pytest

from quark.config import RetryConfig
from quark.core.context import SemanticTask
from quark.models.semantic import SemanticError, SemanticRuntime
from quark.state.database import StateDatabase
from skills.obsidian.operations.parse_frontmatter import (
    FrontmatterStatus,
    parse_frontmatter,
)
from skills.obsidian.operations.read_note import read_note
from skills.obsidian.semantic import ConfidenceRoute, classify_project


def test_edge_notes_are_read_without_content_loss(
    vault_path: Path, tmp_path: Path
) -> None:
    empty = vault_path / "Empty.md"
    unicode_note = vault_path / "Ünicode note.md"
    duplicate = vault_path / "Duplicate.md"
    empty.write_bytes(b"")
    unicode_note.write_text(
        "---\ntags: [café]\n---\nこんにちは #世界\n", encoding="utf-8"
    )
    duplicate.write_text("---\ntags: [one]\ntags: [two]\n---\nbody\n", encoding="utf-8")
    try:
        assert read_note(vault_path, empty.name).original_bytes == b""
        assert read_note(vault_path, unicode_note.name).content_hash
        assert (
            parse_frontmatter(read_note(vault_path, duplicate.name)).status
            is FrontmatterStatus.VALID
        )
        huge = vault_path / "Huge.md"
        huge.write_text("x" * 100_000, encoding="utf-8")
        assert len(read_note(vault_path, huge.name).content) == 100_000
    finally:
        for path in (empty, unicode_note, duplicate, vault_path / "Huge.md"):
            path.unlink(missing_ok=True)


def test_transaction_rolls_back_on_failure(tmp_path: Path) -> None:
    with StateDatabase(tmp_path / "state.db") as state:
        with pytest.raises(RuntimeError), state.transaction() as connection:
            connection.execute(
                "INSERT INTO facts(fact_key,value_json,created_at,updated_at) "
                "VALUES('x','{}',datetime('now'),datetime('now'))"
            )
            raise RuntimeError("abort")
        assert (
            state.connection.execute(
                "SELECT COUNT(*) FROM facts WHERE fact_key='x'"
            ).fetchone()[0]
            == 0
        )


def test_low_confidence_classification_escalates(vault_path: Path) -> None:
    document = read_note(vault_path, "Inbox/Meeting Note.md")

    class Provider:
        provider_name = "fake"
        model_name = "tiny"

        def generate(self, prompt: str, **kwargs):
            from quark.models.provider import ModelResponse

            return ModelResponse('{"choice":"none","confidence":0.1}')

        def health_check(self, **kwargs):
            return True

    decision = classify_project(
        SemanticRuntime(
            Provider(),
            retries=RetryConfig(max_attempts=1),
            prompt_root=Path("skills/obsidian/prompts"),
        ),
        document,
        ["sanctions-paper"],
    )
    assert decision.route is ConfidenceRoute.ASK_USER


def test_invalid_model_output_has_explicit_error() -> None:
    class Provider:
        provider_name = "fake"
        model_name = "tiny"

        def generate(self, prompt: str, **kwargs):
            from quark.models.provider import ModelResponse

            return ModelResponse("prose instead of json")

        def health_check(self, **kwargs):
            return True

    runtime = SemanticRuntime(Provider(), retries=RetryConfig(max_attempts=1))
    task = SemanticTask(
        operation="x",
        role="",
        facts={},
        choices=(),
        output_schema=None,
        content_hash="h",
        model_policy={},
    )
    with pytest.raises(SemanticError, match="failed after 1 attempts"):
        runtime.run(task)


def test_frontmatter_parser_fuzzish_delimiters(vault_path: Path) -> None:
    for index in range(25):
        path = vault_path / f"Fuzz {index}.md"
        path.write_text(
            f"---\ntitle: note-{index}\n---\nbody {index}\n", encoding="utf-8"
        )
        try:
            parsed = parse_frontmatter(read_note(vault_path, path.name))
            assert parsed.status is FrontmatterStatus.VALID
            assert parsed.data["title"] == f"note-{index}"
        finally:
            path.unlink(missing_ok=True)
