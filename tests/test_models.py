from pydantic import ValidationError
import pytest

from quark.models import (
    InteractionType,
    InvalidStateTransitionError,
    PendingInteraction,
    Run,
    Session,
    SkillCall,
    SkillCallStatus,
    SkillError,
)


def test_skill_call_records_immutable_argument_revisions() -> None:
    original = {"text": "a", "nested": {"value": 1}}
    call = SkillCall(run_id="run_1", skill_name="test.echo", arguments=original)

    call.transition(SkillCallStatus.PROPOSED)
    call.transition(SkillCallStatus.SCHEMA_VALIDATING)
    call.transition(SkillCallStatus.VALIDATING)
    call.transition(SkillCallStatus.AWAITING_REVIEW)
    call.revise({"text": "b"}, source="user")
    original["nested"]["value"] = 2

    assert call.status is SkillCallStatus.REVISED
    assert [revision.arguments for revision in call.revisions] == [
        {"text": "a", "nested": {"value": 1}},
        {"text": "b"},
    ]
    assert call.revisions[1].revision == 2
    with pytest.raises(ValidationError):
        call.revisions[0].revision = 9


def test_core_models_are_typed_and_serializable() -> None:
    run = Run(session_id="cli:default", original_request="echo hello")
    call = SkillCall(run_id=run.id, skill_name="test.echo", arguments={"text": "hello"})
    pending = PendingInteraction(
        type=InteractionType.REVIEW_CALL,
        run_id=run.id,
        skill_call_id=call.id,
        original_request=run.original_request,
        proposed_call=call,
    )
    session = Session(session_key="cli:default", active_run_id=run.id, pending_interaction=pending)

    restored = Session.model_validate_json(session.model_dump_json())

    assert restored.pending_interaction is not None
    assert restored.pending_interaction.proposed_call.skill_name == "test.echo"


def test_skill_error_rejects_unstructured_extra_fields() -> None:
    with pytest.raises(ValidationError):
        SkillError(code="BAD", message="bad", surprise=True)


def test_invalid_skill_call_transition_is_rejected() -> None:
    call = SkillCall(run_id="run_1", skill_name="test.echo", arguments={})

    with pytest.raises(InvalidStateTransitionError):
        call.transition(SkillCallStatus.COMPLETED)
