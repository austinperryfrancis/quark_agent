import asyncio
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict
import pytest

from quark.inference import (
    ArgumentDecision,
    InteractionRelationship,
    InteractionRelationshipDecision,
    Message,
    ModelProvider,
    ValidationDecision,
    ValidationOutcome,
)
from quark.inference.prompts import (
    CLASSIFY_RESPONSE_PROMPT_VERSION,
    EDIT_ARGUMENTS_PROMPT_VERSION,
)
from quark.models import (
    InteractionType,
    ReviewPolicy,
    RunStatus,
    SideEffect,
    SkillCallStatus,
)
from quark.runtime import PendingInteractionNotFoundError, SkillRunner
from quark.skills import Skill, SkillContext, SkillRegistry


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class TextOutput(BaseModel):
    text: str


class RecordingSkill(Skill):
    name = "test.write"
    description = "Write text locally."
    input_schema = TextInput
    output_schema = TextOutput
    side_effect = SideEffect.LOCAL_WRITE

    def __init__(self) -> None:
        self.executions: list[str] = []

    async def run(self, ctx: SkillContext, args: BaseModel) -> TextOutput:
        assert isinstance(args, TextInput)
        self.executions.append(args.text)
        return TextOutput(text=args.text)


class ParentSkill(Skill):
    name = "test.parent"
    description = "Delegate a write."
    input_schema = TextInput
    output_schema = TextOutput
    allowed_children = ("test.write",)
    side_effect = SideEffect.READ_ONLY

    async def run(self, ctx: SkillContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, TextInput)
        return await ctx.call_skill("test.write", {"text": args.text})


class QueueProvider(ModelProvider):
    def __init__(self, *outputs: BaseModel) -> None:
        self.outputs = deque(outputs)
        self.calls: list[tuple[tuple[Message, ...], type[BaseModel]]] = []

    async def generate_structured(
        self,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
        options: dict[str, Any] | None = None,
    ) -> BaseModel:
        self.calls.append((messages, schema))
        output = self.outputs.popleft()
        assert isinstance(output, schema)
        return output


def make_runner(skill: Skill, provider: ModelProvider | None = None) -> SkillRunner:
    registry = SkillRegistry()
    registry.register(skill)
    return SkillRunner(registry, provider=provider)


def test_write_call_waits_for_review_and_exact_approval_executes() -> None:
    skill = RecordingSkill()
    runner = make_runner(skill)

    call = asyncio.run(
        runner.run(
            "test.write",
            {"text": "hello"},
            session_id="cli:default",
            original_request="Write hello.",
        )
    )

    pending = runner.sessions["cli:default"].pending_interaction
    assert call.status is SkillCallStatus.AWAITING_REVIEW
    assert skill.executions == []
    assert pending is not None
    assert pending.type is InteractionType.REVIEW_CALL
    assert pending.skill_call_id == call.id
    assert pending.proposed_call.arguments == {"text": "hello"}

    completed = asyncio.run(runner.handle_response("cli:default", "yes"))

    assert completed is call
    assert call.status is SkillCallStatus.COMPLETED
    assert call.review_status == "APPROVED"
    assert skill.executions == ["hello"]
    assert runner.sessions["cli:default"].pending_interaction is None
    assert runner.runs[call.run_id].status is RunStatus.COMPLETED


@pytest.mark.parametrize(
    ("response", "status", "run_status"),
    [
        ("no", SkillCallStatus.REJECTED, RunStatus.FAILED),
        ("cancel", SkillCallStatus.CANCELLED, RunStatus.CANCELLED),
    ],
)
def test_exact_rejection_and_cancellation_never_execute(
    response: str, status: SkillCallStatus, run_status: RunStatus
) -> None:
    skill = RecordingSkill()
    runner = make_runner(skill)
    call = asyncio.run(
        runner.run("test.write", {"text": "hello"}, session_id="cli:default")
    )

    resolved = asyncio.run(runner.handle_response("cli:default", response))

    assert resolved is call
    assert call.status is status
    assert skill.executions == []
    assert runner.runs[call.run_id].status is run_status


def test_natural_language_edit_revalidates_and_returns_to_review() -> None:
    skill = RecordingSkill()
    provider = QueueProvider(
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        InteractionRelationshipDecision(relationship=InteractionRelationship.EDIT),
        ArgumentDecision(arguments={"text": "revised"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runner = make_runner(skill, provider)
    call = asyncio.run(
        runner.run(
            "test.write",
            {"text": "original"},
            session_id="cli:default",
            original_request="Write the text.",
        )
    )
    old_pending_id = runner.sessions["cli:default"].pending_interaction.id

    revised = asyncio.run(
        runner.handle_response("cli:default", "Use revised instead.")
    )

    new_pending = runner.sessions["cli:default"].pending_interaction
    assert revised is call
    assert call.status is SkillCallStatus.AWAITING_REVIEW
    assert call.arguments == {"text": "revised"}
    assert call.revisions[-1].source == "user"
    assert skill.executions == []
    assert old_pending_id not in runner.pending_interactions
    assert new_pending.id != old_pending_id
    assert [schema for _, schema in provider.calls] == [
        ValidationDecision,
        InteractionRelationshipDecision,
        ArgumentDecision,
        ValidationDecision,
    ]
    edit_prompt = provider.calls[2][0][0].content
    classify_prompt = provider.calls[1][0][0].content
    assert '"text": "original"' in classify_prompt
    assert '"text": "original"' in edit_prompt
    assert "Use revised instead." in edit_prompt
    assert CLASSIFY_RESPONSE_PROMPT_VERSION == "classify_response.v1"
    assert EDIT_ARGUMENTS_PROMPT_VERSION == "edit_arguments.v1"

    asyncio.run(runner.handle_response("cli:default", "approve"))
    assert skill.executions == ["revised"]


def test_validator_question_can_be_answered_and_revalidated() -> None:
    skill = RecordingSkill()
    skill.side_effect = SideEffect.READ_ONLY
    provider = QueueProvider(
        ValidationDecision(
            decision=ValidationOutcome.ASK_USER,
            question="Which exact text?",
        ),
        InteractionRelationshipDecision(relationship=InteractionRelationship.ANSWER),
        ArgumentDecision(arguments={"text": "specific"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runner = make_runner(skill, provider)

    call = asyncio.run(
        runner.run("test.write", {"text": "it"}, session_id="cli:default")
    )
    pending = runner.sessions["cli:default"].pending_interaction

    assert call.status is SkillCallStatus.AWAITING_INPUT
    assert pending.type is InteractionType.ASK_USER
    assert pending.question == "Which exact text?"

    completed = asyncio.run(
        runner.handle_response("cli:default", "Use the word specific.")
    )

    assert completed.status is SkillCallStatus.COMPLETED
    assert completed.result == TextOutput(text="specific")


def test_new_request_does_not_discard_pending_review() -> None:
    skill = RecordingSkill()
    provider = QueueProvider(
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        InteractionRelationshipDecision(
            relationship=InteractionRelationship.NEW_REQUEST
        ),
    )
    runner = make_runner(skill, provider)
    call = asyncio.run(
        runner.run("test.write", {"text": "hello"}, session_id="cli:default")
    )
    pending = runner.sessions["cli:default"].pending_interaction

    result = asyncio.run(
        runner.handle_response("cli:default", "What meetings are tomorrow?")
    )

    assert result is None
    assert call.status is SkillCallStatus.AWAITING_REVIEW
    assert runner.sessions["cli:default"].pending_interaction is pending


def test_review_policy_and_side_effect_defaults_are_deterministic() -> None:
    never = RecordingSkill()
    never.review_policy = ReviewPolicy.NEVER
    never_call = asyncio.run(make_runner(never).run("test.write", {"text": "safe"}))
    assert never_call.status is SkillCallStatus.COMPLETED

    risk = RecordingSkill()
    risk.review_policy = ReviewPolicy.RISK_BASED
    risk.side_effect = SideEffect.DESTRUCTIVE
    risk_call = asyncio.run(make_runner(risk).run("test.write", {"text": "risky"}))
    assert risk_call.status is SkillCallStatus.AWAITING_REVIEW

    optional_read = RecordingSkill()
    optional_read.side_effect = SideEffect.READ_ONLY
    read_call = asyncio.run(
        make_runner(optional_read).run("test.write", {"text": "read"})
    )
    assert read_call.status is SkillCallStatus.COMPLETED


def test_response_without_pending_interaction_is_rejected() -> None:
    runner = make_runner(RecordingSkill())

    with pytest.raises(PendingInteractionNotFoundError):
        asyncio.run(runner.handle_response("cli:missing", "yes"))


def test_child_review_is_independent_and_becomes_session_pending_call() -> None:
    child = RecordingSkill()
    registry = SkillRegistry()
    registry.register(child)
    registry.register(ParentSkill())
    runner = SkillRunner(registry)

    parent = asyncio.run(
        runner.run(
            "test.parent", {"text": "nested"}, session_id="cli:default"
        )
    )
    pending = runner.sessions["cli:default"].pending_interaction
    child_call = next(
        call for call in runner.calls.values() if call.parent_call_id == parent.id
    )

    assert parent.status is SkillCallStatus.AWAITING_REVIEW
    assert child_call.status is SkillCallStatus.AWAITING_REVIEW
    assert pending.skill_call_id == child_call.id
    assert child.executions == []
