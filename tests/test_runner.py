import asyncio

from pydantic import BaseModel, ConfigDict
import pytest

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
    description = "Echo typed text."
    input_schema = EchoInput
    output_schema = EchoOutput

    async def run(self, ctx: SkillContext, args: BaseModel) -> EchoOutput:
        assert isinstance(args, EchoInput)
        return EchoOutput(text=args.text)


class ParentSkill(Skill):
    name = "test.parent"
    description = "Invoke test.echo through the runtime."
    input_schema = EchoInput
    output_schema = EchoOutput
    allowed_children = ("test.echo",)

    async def run(self, ctx: SkillContext, args: BaseModel) -> BaseModel:
        assert isinstance(args, EchoInput)
        return await ctx.call_skill("test.echo", {"text": args.text})


class ForbiddenParentSkill(ParentSkill):
    name = "test.forbidden_parent"
    allowed_children = ()


class InvalidChildParentSkill(ParentSkill):
    name = "test.invalid_child_parent"

    async def run(self, ctx: SkillContext, args: BaseModel) -> BaseModel:
        return await ctx.call_skill(
            "test.echo", {"text": "ok", "unexpected": True}
        )


class InvalidOutputSkill(EchoSkill):
    name = "test.invalid_output"

    async def run(self, ctx: SkillContext, args: BaseModel) -> dict:
        return {"wrong": "field"}


def make_runner(*skills: Skill) -> SkillRunner:
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return SkillRunner(registry)


def test_atomic_skill_call_completes_with_typed_result() -> None:
    runner = make_runner(EchoSkill())

    call = asyncio.run(runner.run("test.echo", {"text": "hello"}))

    assert call.status is SkillCallStatus.COMPLETED
    assert call.result == EchoOutput(text="hello")
    assert runner.runs[call.run_id].status is RunStatus.COMPLETED


def test_composite_skill_recurses_only_through_runtime() -> None:
    runner = make_runner(EchoSkill(), ParentSkill())

    parent = asyncio.run(runner.run("test.parent", {"text": "nested"}))
    children = [call for call in runner.calls.values() if call.parent_call_id == parent.id]

    assert parent.result == EchoOutput(text="nested")
    assert len(children) == 1
    assert children[0].skill_name == "test.echo"
    assert children[0].depth == 1
    assert children[0].proposed_by == "test.parent"
    assert children[0].status is SkillCallStatus.COMPLETED


def test_runtime_rejects_undeclared_child() -> None:
    runner = make_runner(EchoSkill(), ForbiddenParentSkill())

    with pytest.raises(SkillRunError) as caught:
        asyncio.run(runner.run("test.forbidden_parent", {"text": "no"}))

    assert caught.value.call.status is SkillCallStatus.FAILED
    assert caught.value.call.error.code == "CHILD_SKILL_NOT_ALLOWED"
    assert runner.runs[caught.value.call.run_id].status is RunStatus.FAILED
    assert not any(call.parent_call_id for call in runner.calls.values())


def test_child_arguments_are_validated_independently() -> None:
    runner = make_runner(EchoSkill(), InvalidChildParentSkill())

    with pytest.raises(SkillRunError) as caught:
        asyncio.run(runner.run("test.invalid_child_parent", {"text": "valid"}))

    parent = caught.value.call
    child = next(call for call in runner.calls.values() if call.parent_call_id == parent.id)
    assert parent.status is SkillCallStatus.FAILED
    assert parent.error.code == "CHILD_SKILL_FAILED"
    assert child.status is SkillCallStatus.FAILED
    assert child.error.code == "INPUT_SCHEMA_VALIDATION_FAILED"


def test_invalid_top_level_arguments_fail_and_preserve_call() -> None:
    runner = make_runner(EchoSkill())

    with pytest.raises(SkillRunError) as caught:
        asyncio.run(runner.run("test.echo", {"wrong": "field"}))

    assert caught.value.call.status is SkillCallStatus.FAILED
    assert caught.value.call.error.code == "INPUT_SCHEMA_VALIDATION_FAILED"
    assert caught.value.call.id in runner.calls


def test_invalid_output_is_distinguished_from_invalid_input() -> None:
    runner = make_runner(InvalidOutputSkill())

    with pytest.raises(SkillRunError) as caught:
        asyncio.run(runner.run("test.invalid_output", {"text": "valid"}))

    assert caught.value.call.error.code == "OUTPUT_SCHEMA_VALIDATION_FAILED"


def test_failure_before_call_creation_finalizes_run() -> None:
    runner = make_runner(EchoSkill())

    with pytest.raises(LookupError):
        asyncio.run(runner.run("test.missing", {}))

    run = next(iter(runner.runs.values()))
    assert run.status is RunStatus.FAILED
    assert run.completed_at is not None
