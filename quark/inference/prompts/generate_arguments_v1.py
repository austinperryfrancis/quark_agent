"""The generate_arguments.v1 prompt."""

from __future__ import annotations

import json

from quark.skills.base import Skill

PROMPT_VERSION = "generate_arguments.v1"


def build_generate_arguments_prompt(request: str, skill: Skill) -> str:
    """Render only the selected Skill contract and original request."""
    schema = json.dumps(skill.input_schema.model_json_schema(), sort_keys=True)
    return (
        f"SKILL\n{skill.name}\n{skill.description}\n\n"
        f"INPUT SCHEMA\n{schema}\n\n"
        f"USER REQUEST\n{request}\n\n"
        "TASK\nReturn arguments for this Skill matching the input schema."
    )
