import asyncio
from collections import deque
from typing import Any

from pydantic import BaseModel, ConfigDict
import pytest

from quark.inference import (
    ArgumentDecision,
    Message,
    ModelProvider,
    ValidationDecision,
    ValidationOutcome,
)
from quark.inference.prompts import (
    GENERATE_ARGUMENTS_PROMPT_VERSION,
    VALIDATE_CALL_PROMPT_VERSION,
)
from quark.models import RunStatus, SkillCallStatus
from quark.runtime import SkillRunError, SkillRunner
from quark.skills import Skill, SkillContext, SkillRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class EchoOutput(BaseModel):
    text: str


class EchoSkill(Skill):
    name = "test.echo"
    description = "Echo text."
    input_schema = EchoInput
    output_schema = EchoOutput
    validator_instructions = "Text must preserve the user's meaning."

    async def run(self, ctx: SkillContext, args: BaseModel) -> EchoOutput:
        assert isinstance(args, EchoInput)
        return EchoOutput(text=args.text)


class ParentSkill(Skill):
    name = "test.parent"
    description = "Call the echo Skill."
    input_schema = EchoInput
    output_schema = EchoOutput
    allowed_children = ("test.echo",)

    async def run(self, ctx: SkillContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, EchoInput)
        return await ctx.call_skill("test.echo", {"text": args.text})


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


def make_runner(provider: ModelProvider, *skills: Skill, revisions: int = 2) -> SkillRunner:
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return SkillRunner(
        registry, provider=provider, max_validation_revisions=revisions
    )


def test_generated_arguments_become_proposal_then_execute_after_approval() -> None:
    provider = QueueProvider(
        ArgumentDecision(arguments={"text": "hello"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runner = make_runner(provider, EchoSkill())

    call = asyncio.run(runner.run_generated("test.echo", "Echo hello."))

    assert call.proposed_by == "model"
    assert call.status is SkillCallStatus.COMPLETED
    assert call.result == EchoOutput(text="hello")
    assert [schema for _, schema in provider.calls] == [
        ArgumentDecision,
        ValidationDecision,
    ]
    generation_prompt = provider.calls[0][0][0].content
    validation_prompt = provider.calls[1][0][0].content
    assert "Echo hello." in generation_prompt
    assert '"text"' in generation_prompt
    assert "Text must preserve the user's meaning." in validation_prompt
    assert GENERATE_ARGUMENTS_PROMPT_VERSION == "generate_arguments.v1"
    assert VALIDATE_CALL_PROMPT_VERSION == "validate_call.v2"


def test_child_validation_prompt_describes_the_call_as_a_parent_step() -> None:
    provider = QueueProvider(
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runner = make_runner(provider, EchoSkill(), ParentSkill())

    asyncio.run(
        runner.run(
            "test.parent",
            {"text": "hello"},
            original_request="Echo hello through the parent.",
        )
    )

    root_prompt = provider.calls[0][0][0].content
    child_prompt = provider.calls[1][0][0].content
    assert "This is the root call" in root_prompt
    assert "child step proposed by test.parent" in child_prompt
    assert "does not need to complete the original request by itself" in child_prompt


def test_invalid_generated_arguments_fail_before_validator() -> None:
    provider = QueueProvider(ArgumentDecision(arguments={"wrong": "field"}))
    runner = make_runner(provider, EchoSkill())

    with pytest.raises(SkillRunError) as caught:
        asyncio.run(runner.run_generated("test.echo", "Echo hello."))

    assert caught.value.call.status is SkillCallStatus.FAILED
    assert caught.value.call.error.code == "INPUT_SCHEMA_VALIDATION_FAILED"
    assert len(provider.calls) == 1


def test_validator_edit_is_revised_schema_checked_and_validated_again() -> None:
    provider = QueueProvider(
        ValidationDecision(
            decision=ValidationOutcome.EDIT,
            revised_arguments={"text": "corrected"},
        ),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runner = make_runner(provider, EchoSkill())

    call = asyncio.run(
        runner.run("test.echo", {"text": "wrong"}, original_request="Echo corrected.")
    )

    assert call.result == EchoOutput(text="corrected")
    assert len(call.revisions) == 2
    assert call.revisions[1].source == "validator"
    assert len(provider.calls) == 2


def test_invalid_validator_edit_fails_deterministically() -> None:
    provider = QueueProvider(
        ValidationDecision(
            decision=ValidationOutcome.EDIT,
            revised_arguments={"unexpected": True},
        )
    )
    runner = make_runner(provider, EchoSkill())

    with pytest.raises(SkillRunError) as caught:
        asyncio.run(runner.run("test.echo", {"text": "valid"}))

    assert caught.value.call.error.code == "INPUT_SCHEMA_VALIDATION_FAILED"
    assert len(provider.calls) == 1


def test_revision_limit_stops_and_waits_for_user() -> None:
    provider = QueueProvider(
        *[
            ValidationDecision(
                decision=ValidationOutcome.EDIT,
                revised_arguments={"text": f"revision-{number}"},
            )
            for number in range(3)
        ]
    )
    runner = make_runner(provider, EchoSkill(), revisions=2)

    call = asyncio.run(runner.run("test.echo", {"text": "initial"}))

    assert call.status is SkillCallStatus.AWAITING_INPUT
    assert call.validation_status == "REVISION_LIMIT_REACHED"
    assert len(call.revisions) == 3
    assert runner.runs[call.run_id].status is RunStatus.WAITING


@pytest.mark.parametrize(
    ("decision", "expected_status", "expected_run_status"),
    [
        (
            ValidationDecision(
                decision=ValidationOutcome.ASK_USER,
                question="Which text?",
            ),
            SkillCallStatus.AWAITING_INPUT,
            RunStatus.WAITING,
        ),
        (
            ValidationDecision(
                decision=ValidationOutcome.REJECT,
                reason="The request does not match the Skill.",
            ),
            SkillCallStatus.REJECTED,
            RunStatus.FAILED,
        ),
    ],
)
def test_non_approval_decisions_never_execute(
    decision: ValidationDecision,
    expected_status: SkillCallStatus,
    expected_run_status: RunStatus,
) -> None:
    provider = QueueProvider(decision)
    runner = make_runner(provider, EchoSkill())

    call = asyncio.run(runner.run("test.echo", {"text": "hello"}))

    assert call.status is expected_status
    assert call.result is None
    assert runner.runs[call.run_id].status is expected_run_status
    assert call.validation_reason == decision.reason
    assert call.validation_question == decision.question


def test_child_calls_receive_independent_validation() -> None:
    provider = QueueProvider(
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runner = make_runner(provider, EchoSkill(), ParentSkill())

    parent = asyncio.run(
        runner.run("test.parent", {"text": "nested"}, original_request="Echo nested.")
    )

    child = next(call for call in runner.calls.values() if call.parent_call_id == parent.id)
    assert parent.status is SkillCallStatus.COMPLETED
    assert child.status is SkillCallStatus.COMPLETED
    assert [schema for _, schema in provider.calls] == [
        ValidationDecision,
        ValidationDecision,
    ]


def test_child_ask_user_propagates_waiting_state_to_parent() -> None:
    provider = QueueProvider(
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        ValidationDecision(
            decision=ValidationOutcome.ASK_USER,
            question="What should the child echo?",
        ),
    )
    runner = make_runner(provider, EchoSkill(), ParentSkill())

    parent = asyncio.run(
        runner.run("test.parent", {"text": "nested"}, original_request="Echo it.")
    )

    child = next(call for call in runner.calls.values() if call.parent_call_id == parent.id)
    assert child.status is SkillCallStatus.AWAITING_INPUT
    assert parent.status is SkillCallStatus.AWAITING_INPUT
    assert parent.validation_question == "What should the child echo?"
    assert runner.runs[parent.run_id].status is RunStatus.WAITING
