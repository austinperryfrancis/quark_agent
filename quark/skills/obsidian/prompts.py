"""Versioned prompt for typed Obsidian tag proposal."""

from __future__ import annotations

import json

from quark.skills.obsidian.models import ProposeTagsInput

PROMPT_VERSION = "obsidian.tags.propose.v1"


def build_tag_prompt(args: ProposeTagsInput) -> str:
    existing = json.dumps(args.existing_tags)
    return (
        f"NOTE\n{args.path}\n\n"
        f"EXISTING TAGS\n{existing}\n\n"
        f"CONTENT\n{args.content}\n\n"
        "TASK\nReturn 1 to 12 concise lowercase tags relevant to this note."
    )
