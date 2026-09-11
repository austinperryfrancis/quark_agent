"""Versioned prompts for Quark's narrow inference decisions."""

from quark.inference.prompts.classify_response_v1 import (
    PROMPT_VERSION as CLASSIFY_RESPONSE_PROMPT_VERSION,
)
from quark.inference.prompts.classify_response_v1 import build_classify_response_prompt
from quark.inference.prompts.edit_arguments_v1 import (
    PROMPT_VERSION as EDIT_ARGUMENTS_PROMPT_VERSION,
)
from quark.inference.prompts.edit_arguments_v1 import build_edit_arguments_prompt
from quark.inference.prompts.generate_arguments_v1 import (
    PROMPT_VERSION as GENERATE_ARGUMENTS_PROMPT_VERSION,
)
from quark.inference.prompts.generate_arguments_v1 import build_generate_arguments_prompt
from quark.inference.prompts.route_skill_v1 import (
    PROMPT_VERSION as ROUTE_SKILL_PROMPT_VERSION,
)
from quark.inference.prompts.route_skill_v1 import build_route_prompt
from quark.inference.prompts.validate_call_v1 import (
    PROMPT_VERSION as VALIDATE_CALL_PROMPT_VERSION,
)
from quark.inference.prompts.validate_call_v1 import build_validate_call_prompt

__all__ = [
    "CLASSIFY_RESPONSE_PROMPT_VERSION",
    "EDIT_ARGUMENTS_PROMPT_VERSION",
    "GENERATE_ARGUMENTS_PROMPT_VERSION",
    "ROUTE_SKILL_PROMPT_VERSION",
    "VALIDATE_CALL_PROMPT_VERSION",
    "build_classify_response_prompt",
    "build_edit_arguments_prompt",
    "build_generate_arguments_prompt",
    "build_route_prompt",
    "build_validate_call_prompt",
]
