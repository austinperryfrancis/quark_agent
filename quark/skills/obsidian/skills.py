"""Initial atomic and composite Obsidian Skills."""

from __future__ import annotations

from quark.inference import InferenceTaskType, Message, MessageRole
from quark.models import ReviewPolicy, SideEffect
from quark.skills.base import Skill, SkillContext
from quark.skills.obsidian.models import (
    ApplyTagsInput,
    ApplyTagsResult,
    InboxLatestInput,
    InboxLatestResult,
    NoteContent,
    NotePathInput,
    OrganizeNoteInput,
    OrganizeNoteResult,
    ProposeTagsInput,
    TagProposal,
)
from quark.skills.obsidian.prompts import build_tag_prompt
from quark.skills.obsidian.vault import ObsidianVault


class ReadNoteSkill(Skill):
    name = "obsidian.note.read"
    description = "Read one Markdown note from the configured Obsidian vault."
    input_schema = NotePathInput
    output_schema = NoteContent
    side_effect = SideEffect.READ_ONLY
    review_policy = ReviewPolicy.NEVER

    def __init__(self, vault: ObsidianVault) -> None:
        self.vault = vault

    async def run(self, ctx: SkillContext, args: NotePathInput) -> NoteContent:
        content = self.vault.read_note(args.path)
        frontmatter, _ = self.vault.read_frontmatter(content)
        tags = frontmatter.get("tags", [])
        if isinstance(tags, str):
            tags = [tags]
        if not isinstance(tags, list):
            tags = []
        return NoteContent(
            path=self.vault.relative(self.vault.resolve_note(args.path)),
            content=content,
            tags=[str(tag) for tag in tags],
        )


class InboxLatestSkill(Skill):
    name = "obsidian.inbox.latest"
    description = "Find the newest Markdown note in the configured Obsidian inbox."
    input_schema = InboxLatestInput
    output_schema = InboxLatestResult
    side_effect = SideEffect.READ_ONLY
    review_policy = ReviewPolicy.NEVER

    def __init__(self, vault: ObsidianVault) -> None:
        self.vault = vault

    async def run(
        self, ctx: SkillContext, args: InboxLatestInput
    ) -> InboxLatestResult:
        return InboxLatestResult(path=self.vault.latest_inbox_note())


class ProposeTagsSkill(Skill):
    name = "obsidian.tags.propose"
    description = "Propose a bounded typed tag list for an Obsidian note."
    input_schema = ProposeTagsInput
    output_schema = TagProposal
    side_effect = SideEffect.READ_ONLY
    review_policy = ReviewPolicy.NEVER

    async def run(self, ctx: SkillContext, args: ProposeTagsInput) -> TagProposal:
        result = await ctx.infer(
            InferenceTaskType.GENERATE_ARGUMENTS,
            (Message(role=MessageRole.USER, content=build_tag_prompt(args)),),
            TagProposal,
        )
        return TagProposal.model_validate(result)


class ApplyTagsSkill(Skill):
    name = "obsidian.tags.apply"
    description = "Replace an Obsidian note's YAML frontmatter tags."
    input_schema = ApplyTagsInput
    output_schema = ApplyTagsResult
    side_effect = SideEffect.LOCAL_WRITE
    review_policy = ReviewPolicy.ALWAYS
    validator_instructions = "Never change a note other than the requested path."

    def __init__(self, vault: ObsidianVault) -> None:
        self.vault = vault

    async def run(self, ctx: SkillContext, args: ApplyTagsInput) -> ApplyTagsResult:
        path = self.vault.relative(self.vault.resolve_note(args.path))
        changed = self.vault.apply_tags(path, args.tags)
        return ApplyTagsResult(path=path, tags=args.tags, changed=changed)


class OrganizeNoteSkill(Skill):
    name = "obsidian.organize_note"
    description = "Organize one Obsidian note, or the newest inbox note, with relevant tags."
    input_schema = OrganizeNoteInput
    output_schema = OrganizeNoteResult
    allowed_children = (
        "obsidian.inbox.latest",
        "obsidian.note.read",
        "obsidian.tags.propose",
        "obsidian.tags.apply",
    )
    side_effect = SideEffect.LOCAL_WRITE
    review_policy = ReviewPolicy.NEVER
    top_level = True

    async def run(self, ctx: SkillContext, args: OrganizeNoteInput) -> OrganizeNoteResult:
        path = args.path
        if path is None:
            latest = await ctx.call_skill("obsidian.inbox.latest", {})
            path = latest.path
        note = await ctx.call_skill("obsidian.note.read", {"path": path})
        proposal = await ctx.call_skill(
            "obsidian.tags.propose",
            {
                "path": note.path,
                "content": note.content,
                "existing_tags": note.tags,
            },
        )
        applied = await ctx.call_skill(
            "obsidian.tags.apply",
            {"path": note.path, "tags": proposal.tags},
        )
        return OrganizeNoteResult(
            path=applied.path,
            tags=applied.tags,
            changed=applied.changed,
        )
