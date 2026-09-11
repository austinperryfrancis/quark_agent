import asyncio
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel
import pytest

from quark.inference import Message, ModelProvider, ValidationDecision, ValidationOutcome
from quark.models import SkillCallStatus
from quark.runtime import SkillRunner
from quark.skills import SkillRegistry
from quark.skills.obsidian import (
    ObsidianVault,
    build_obsidian_skills,
    register_obsidian_skills,
)
from quark.skills.obsidian.models import TagProposal
from quark.skills.obsidian.prompts import PROMPT_VERSION
from quark.skills.obsidian.vault import NoteNotFoundError, VaultPathError


class QueueProvider(ModelProvider):
    def __init__(self, *outputs: BaseModel) -> None:
        self.outputs = list(outputs)
        self.calls: list[type[BaseModel]] = []

    async def generate_structured(
        self,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
        options: dict[str, Any] | None = None,
    ) -> BaseModel:
        self.calls.append(schema)
        output = self.outputs.pop(0)
        assert isinstance(output, schema)
        return output


def make_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "Vault"
    (vault / "Inbox").mkdir(parents=True)
    return vault


def test_vault_restricts_paths_and_reads_frontmatter(tmp_path) -> None:
    vault_path = make_vault(tmp_path)
    note = vault_path / "Inbox" / "Note.md"
    note.write_text("---\nproject: quark\ntags: [old]\n---\nBody\n")
    vault = ObsidianVault(vault_path)

    content = vault.read_note("Inbox/Note")
    frontmatter, body = vault.read_frontmatter(content)

    assert frontmatter == {"project": "quark", "tags": ["old"]}
    assert body == "Body\n"
    with pytest.raises(VaultPathError):
        vault.resolve_note("../outside.md")
    with pytest.raises(NoteNotFoundError):
        vault.read_note("Inbox/Missing.md")


def test_vault_rejects_symlink_escape(tmp_path) -> None:
    vault_path = make_vault(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_text("secret")
    os.symlink(outside, vault_path / "Inbox" / "link.md")

    with pytest.raises(VaultPathError):
        ObsidianVault(vault_path).resolve_note("Inbox/link.md")


def test_latest_inbox_note_is_selected_deterministically(tmp_path) -> None:
    vault_path = make_vault(tmp_path)
    older = vault_path / "Inbox" / "Older.md"
    newer = vault_path / "Inbox" / "Newer.md"
    older.write_text("old")
    newer.write_text("new")
    os.utime(older, ns=(1_000_000_000, 1_000_000_000))
    os.utime(newer, ns=(2_000_000_000, 2_000_000_000))

    assert ObsidianVault(vault_path).latest_inbox_note() == "Inbox/Newer.md"


def test_tag_application_is_reviewed_atomic_and_preserves_note_body(tmp_path) -> None:
    vault_path = make_vault(tmp_path)
    note = vault_path / "Inbox" / "Note.md"
    note.write_text("---\nproject: quark\ntags: [old]\n---\nBody stays.\n")
    note.chmod(0o640)
    registry = SkillRegistry()
    register_obsidian_skills(registry, vault_path)
    runner = SkillRunner(registry)

    call = asyncio.run(
        runner.run(
            "obsidian.tags.apply",
            {"path": "Inbox/Note.md", "tags": ["New Tag", "quark"]},
            session_id="cli:default",
        )
    )

    assert call.status is SkillCallStatus.AWAITING_REVIEW
    assert "tags: [old]" in note.read_text()

    completed = asyncio.run(runner.handle_response("cli:default", "approve"))
    updated = note.read_text()
    assert completed.status is SkillCallStatus.COMPLETED
    assert completed.result.tags == ["new-tag", "quark"]
    assert "project: quark" in updated
    assert "- new-tag" in updated
    assert updated.endswith("Body stays.\n")
    assert note.stat().st_mode & 0o777 == 0o640
    assert not list(note.parent.glob(".Note.md.*.tmp"))


def test_tag_proposal_uses_one_typed_task_local_inference(tmp_path) -> None:
    vault_path = make_vault(tmp_path)
    provider = QueueProvider(
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        TagProposal(tags=["Research", "Supply Chain"]),
    )
    registry = SkillRegistry()
    for skill in build_obsidian_skills(vault_path):
        registry.register(skill)
    runner = SkillRunner(registry, provider=provider)

    call = asyncio.run(
        runner.run(
            "obsidian.tags.propose",
            {
                "path": "Inbox/Note.md",
                "content": "Research about supply chains.",
                "existing_tags": [],
            },
        )
    )

    assert call.result == TagProposal(tags=["research", "supply-chain"])
    assert provider.calls == [ValidationDecision, TagProposal]
    assert PROMPT_VERSION == "obsidian.tags.propose.v1"


def test_organize_note_recurses_to_independently_reviewed_apply(tmp_path) -> None:
    vault_path = make_vault(tmp_path)
    note = vault_path / "Inbox" / "Newest.md"
    note.write_text("A note about sanctions research.\n")
    provider = QueueProvider(
        # Parent, latest, read, proposal call validation.
        *[ValidationDecision(decision=ValidationOutcome.APPROVE) for _ in range(4)],
        # Typed semantic tag proposal.
        TagProposal(tags=["sanctions", "research"]),
        # Apply call validation; human review follows independently.
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    registry = SkillRegistry()
    register_obsidian_skills(registry, vault_path)
    registry.validate_graph()
    runner = SkillRunner(registry, provider=provider)

    parent = asyncio.run(
        runner.run(
            "obsidian.organize_note",
            {"path": None},
            session_id="cli:default",
            original_request="Organize my newest inbox note.",
        )
    )
    pending = runner.sessions["cli:default"].pending_interaction
    apply_call = next(
        call
        for call in runner.calls.values()
        if call.skill_name == "obsidian.tags.apply"
    )

    assert parent.status is SkillCallStatus.AWAITING_REVIEW
    assert apply_call.status is SkillCallStatus.AWAITING_REVIEW
    assert pending.skill_call_id == apply_call.id
    assert note.read_text() == "A note about sanctions research.\n"


def test_obsidian_registry_exposes_only_organizer_at_root(tmp_path) -> None:
    registry = SkillRegistry()
    register_obsidian_skills(registry, make_vault(tmp_path))
    registry.validate_graph()

    assert [skill.name for skill in registry.top_level()] == [
        "obsidian.organize_note"
    ]
