"""The validate_call.v2 prompt with explicit root-versus-child semantics."""

from __future__ import annotations

import json

from quark.models import SkillCall
from quark.skills.base import Skill

PROMPT_VERSION = "validate_call.v2"


def build_validate_call_prompt(
    request: str, skill: Skill, call: SkillCall
) -> str:
    """Render the minimum context needed to validate one proposed call."""
    arguments = json.dumps(call.arguments, sort_keys=True)
    instructions = (
        f"\n\nSKILL CONSTRAINTS\n{skill.validator_instructions.strip()}"
        if skill.validator_instructions
        else ""
    )
    if call.parent_call_id is None:
        role = (
            "This is the root call. Validate that it represents the original "
            "request and that its arguments are appropriate."
        )
    else:
        role = (
            f"This is a child step proposed by {call.proposed_by}. Validate that "
            "it is a reasonable step toward the original request and that its "
            "arguments fit this child Skill. It does not need to complete the "
            "original request by itself."
        )
    return (
        f"ORIGINAL REQUEST\n{request}\n\n"
        f"CALL ROLE\n{role}\n\n"
        f"SKILL\n{skill.name}\n{skill.description}\n\n"
        f"PROPOSED ARGUMENTS\n{arguments}"
        f"{instructions}\n\n"
        "TASK\nReturn APPROVE, EDIT, ASK_USER, or REJECT."
    )
