"""The edit_arguments.v1 prompt."""

import json

from quark.models import SkillCall
from quark.skills.base import Skill

PROMPT_VERSION = "edit_arguments.v1"


def build_edit_arguments_prompt(
    call: SkillCall, skill: Skill, feedback: str
) -> str:
    arguments = json.dumps(call.arguments, sort_keys=True)
    schema = json.dumps(skill.input_schema.model_json_schema(), sort_keys=True)
    return (
        f"SKILL\n{skill.name}\n\n"
        f"INPUT SCHEMA\n{schema}\n\n"
        f"CURRENT ARGUMENTS\n{arguments}\n\n"
        f"USER FEEDBACK\n{feedback}\n\n"
        "TASK\nReturn the complete corrected arguments matching the schema."
    )
