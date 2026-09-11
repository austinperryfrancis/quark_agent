import asyncio
import logging

from pydantic import BaseModel, ConfigDict

from quark.models import ReviewPolicy
from quark.runtime import SkillRunner
from quark.skills import Skill, SkillContext, SkillRegistry


class EchoInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class EchoOutput(BaseModel):
    text: str


class EchoSkill(Skill):
    name = "test.echo"
    description = "Echo test text."
    input_schema = EchoInput
    output_schema = EchoOutput
    review_policy = ReviewPolicy.NEVER

    async def run(self, ctx: SkillContext, args: BaseModel) -> EchoOutput:
        assert isinstance(args, EchoInput)
        return EchoOutput(text=args.text)


def test_skill_lifecycle_is_written_to_human_readable_log(caplog) -> None:
    registry = SkillRegistry()
    registry.register(EchoSkill())
    runner = SkillRunner(registry)

    with caplog.at_level(logging.INFO, logger="quark.runtime.runner"):
        call = asyncio.run(runner.run("test.echo", {"text": "hello"}))

    messages = [record.getMessage() for record in caplog.records]
    assert any(f"CALL_PROPOSED run={call.run_id} call={call.id}" in item for item in messages)
    assert any(f"CALL_STARTED run={call.run_id} call={call.id}" in item for item in messages)
    assert any(f"CALL_COMPLETED run={call.run_id} call={call.id}" in item for item in messages)
