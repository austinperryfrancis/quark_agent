from pydantic import ValidationError
import pytest

from quark.inference import (
    ResultValidationDecision,
    ResultValidationOutcome,
    RouteDecision,
    ValidationDecision,
    ValidationOutcome,
)


def test_route_decision_requires_one_outcome() -> None:
    assert RouteDecision(skill="test.echo").skill == "test.echo"
    assert RouteDecision(unsupported=True).unsupported

    with pytest.raises(ValidationError):
        RouteDecision()
    with pytest.raises(ValidationError):
        RouteDecision(skill="test.echo", unsupported=True)


def test_validation_edit_requires_revised_arguments() -> None:
    with pytest.raises(ValidationError):
        ValidationDecision(decision=ValidationOutcome.EDIT)

    decision = ValidationDecision(
        decision=ValidationOutcome.EDIT,
        revised_arguments={"text": "fixed"},
    )
    assert decision.revised_arguments == {"text": "fixed"}


def test_ask_user_decisions_require_questions() -> None:
    with pytest.raises(ValidationError):
        ResultValidationDecision(decision=ResultValidationOutcome.ASK_USER)
