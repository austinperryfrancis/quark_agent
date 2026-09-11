"""Initial deterministic Obsidian Skill tree."""

from pathlib import Path

from quark.skills.base import Skill
from quark.skills.obsidian.skills import (
    ApplyTagsSkill,
    InboxLatestSkill,
    OrganizeNoteSkill,
    ProposeTagsSkill,
    ReadNoteSkill,
)
from quark.skills.obsidian.vault import ObsidianVault
from quark.skills.registry import SkillRegistry


def build_obsidian_skills(
    vault_path: str | Path, *, inbox_path: str = "Inbox"
) -> tuple[Skill, ...]:
    vault = ObsidianVault(vault_path, inbox_path=inbox_path)
    return (
        ReadNoteSkill(vault),
        InboxLatestSkill(vault),
        ProposeTagsSkill(),
        ApplyTagsSkill(vault),
        OrganizeNoteSkill(),
    )


def register_obsidian_skills(
    registry: SkillRegistry,
    vault_path: str | Path,
    *,
    inbox_path: str = "Inbox",
) -> None:
    for skill in build_obsidian_skills(vault_path, inbox_path=inbox_path):
        registry.register(skill)


__all__ = [
    "ApplyTagsSkill",
    "InboxLatestSkill",
    "ObsidianVault",
    "OrganizeNoteSkill",
    "ProposeTagsSkill",
    "ReadNoteSkill",
    "build_obsidian_skills",
    "register_obsidian_skills",
]
