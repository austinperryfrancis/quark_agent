"""The route_skill.v1 prompt."""

from __future__ import annotations

from quark.skills.registry import SkillMetadata

PROMPT_VERSION = "route_skill.v1"


def build_route_prompt(
    request: str, skills: tuple[SkillMetadata, ...]
) -> str:
    """Render only the context required to choose one top-level Skill."""
    available = "\n\n".join(
        f"{skill.name}\n{skill.description}" for skill in skills
    )
    return (
        f"USER REQUEST\n{request}\n\n"
        f"AVAILABLE SKILLS\n\n{available}\n\n"
        "TASK\nChoose exactly one listed Skill. "
        "If none reasonably fits, mark the request unsupported."
    )
