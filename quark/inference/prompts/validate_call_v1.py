"""The validate_call.v1 prompt."""

from __future__ import annotations

import json

from quark.models import SkillCall
from quark.skills.base import Skill

PROMPT_VERSION = "validate_call.v1"


def build_validate_call_prompt(
    request: str, skill: Skill, call: SkillCall
) -> str:
    """Render the request and one proposed call for semantic validation."""
    arguments = json.dumps(call.arguments, sort_keys=True)
    instructions = (
        f"\n\nSKILL CONSTRAINTS\n{skill.validator_instructions.strip()}"
        if skill.validator_instructions
        else ""
    )
    return (
        f"ORIGINAL REQUEST\n{request}\n\n"
        f"SKILL\n{skill.name}\n{skill.description}\n\n"
        f"PROPOSED ARGUMENTS\n{arguments}"
        f"{instructions}\n\n"
        "TASK\nDecide whether this call represents the request. "
        "Return APPROVE, EDIT, ASK_USER, or REJECT."
    )
