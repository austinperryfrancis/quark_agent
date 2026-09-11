"""Versioned prompt for typed Obsidian tag proposal."""

from __future__ import annotations

import json

from quark.skills.obsidian.models import ProposeTagsInput

PROMPT_VERSION = "obsidian.tags.propose.v2"


def build_tag_prompt(args: ProposeTagsInput) -> str:
    existing = json.dumps(args.existing_tags)
    return (
        f"NOTE\n{args.path}\n\n"
        f"EXISTING TAGS\n{existing}\n\n"
        f"CONTENT\n{args.content}\n\n"
        "TASK\nReturn 3 to 5 concise, reusable topic tags in lowercase "
        "kebab-case. Retain an existing tag when it still directly describes a "
        "central subject of the note. Do not return generic labels such as "
        "notes, information, content, "
        "or analysis. Do not return near-duplicates, synonyms, or suffix variants "
        "of the same concept."
    )
