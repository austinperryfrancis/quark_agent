"""The classify_response.v1 prompt."""

import json

from quark.models import PendingInteraction

PROMPT_VERSION = "classify_response.v1"


def build_classify_response_prompt(
    pending: PendingInteraction, message: str
) -> str:
    arguments = (
        json.dumps(pending.proposed_call.arguments, sort_keys=True)
        if pending.proposed_call
        else "{}"
    )
    return (
        f"PENDING INTERACTION\n{pending.type.value}\n"
        f"{pending.question or ''}\n"
        f"CURRENT ARGUMENTS\n{arguments}\n\n"
        f"NEW MESSAGE\n{message}\n\n"
        "TASK\nClassify the message as APPROVAL, REJECTION, CANCELLATION, "
        "EDIT, ANSWER, or NEW_REQUEST."
    )
