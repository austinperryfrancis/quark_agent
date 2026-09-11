import asyncio
from collections import deque
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict

from quark.gateways import (
    GatewayCommand,
    GatewayRequest,
    LocalRuntimeClient,
)
from quark.inference import (
    ArgumentDecision,
    InteractionRelationship,
    InteractionRelationshipDecision,
    Message,
    ModelProvider,
    RootRouter,
    RouteDecision,
    ValidationDecision,
    ValidationOutcome,
)
from quark.models import SideEffect
from quark.runtime import QuarkRuntime, RuntimeService, SkillRunner
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
    top_level = True

    async def run(self, ctx: SkillContext, args: BaseModel) -> EchoOutput:
        assert isinstance(args, EchoInput)
        return EchoOutput(text=args.text)


class WriteSkill(EchoSkill):
    name = "test.write"
    description = "Write text."
    side_effect = SideEffect.LOCAL_WRITE


class ConcurrentEchoSkill(EchoSkill):
    name = "test.concurrent"

    def __init__(self) -> None:
        self.active = 0
        self.maximum_active = 0

    async def run(self, ctx: SkillContext, args: BaseModel) -> EchoOutput:
        assert isinstance(args, EchoInput)
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        return EchoOutput(text=args.text)


class QueueProvider(ModelProvider):
    def __init__(self, *outputs: BaseModel) -> None:
        self.outputs = deque(outputs)

    async def generate_structured(
        self,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
        options: dict[str, Any] | None = None,
    ) -> BaseModel:
        output = self.outputs.popleft()
        assert isinstance(output, schema)
        return output


def make_runtime(provider: ModelProvider, *skills: Skill) -> QuarkRuntime:
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return QuarkRuntime(
        registry,
        RootRouter(registry, provider),
        SkillRunner(registry, provider=provider),
    )


def test_runtime_routes_generates_validates_and_executes_one_skill() -> None:
    provider = QueueProvider(
        RouteDecision(skill="test.echo"),
        ArgumentDecision(arguments={"text": "hello"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runtime = make_runtime(provider, EchoSkill())

    response = asyncio.run(runtime.handle_message("cli:default", "Echo hello."))

    assert response.ok
    assert response.kind == "COMPLETED"
    assert response.data == {"result": {"text": "hello"}}
    assert len(runtime.runner.runs) == 1


def test_runtime_resolves_pending_review_before_root_routing() -> None:
    provider = QueueProvider(
        RouteDecision(skill="test.write"),
        ArgumentDecision(arguments={"text": "hello"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runtime = make_runtime(provider, WriteSkill())

    proposed = asyncio.run(runtime.handle_message("cli:default", "Write hello."))
    completed = asyncio.run(runtime.handle_message("cli:default", "yes"))

    assert proposed.kind == "REVIEW_CALL"
    assert proposed.data["arguments"] == {"text": "hello"}
    assert completed.kind == "COMPLETED"


def test_unrelated_request_can_route_while_review_remains_pending() -> None:
    provider = QueueProvider(
        RouteDecision(skill="test.write"),
        ArgumentDecision(arguments={"text": "hello"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        # Pending-response classification.
        InteractionRelationshipDecision(
            relationship=InteractionRelationship.NEW_REQUEST
        ),
        RouteDecision(skill="test.echo"),
        ArgumentDecision(arguments={"text": "second"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    runtime = make_runtime(provider, WriteSkill(), EchoSkill())
    asyncio.run(runtime.handle_message("cli:default", "Write hello."))
    pending = runtime.runner.sessions["cli:default"].pending_interaction

    response = asyncio.run(runtime.handle_message("cli:default", "Echo second."))

    assert response.kind == "COMPLETED"
    assert runtime.runner.sessions["cli:default"].pending_interaction is pending


def test_capability_question_is_answered_without_model_routing() -> None:
    runtime = make_runtime(QueueProvider(), EchoSkill())

    response = asyncio.run(
        runtime.handle_message("cli:default", "What skills do you have?")
    )

    assert response.kind == "SKILLS"
    assert response.data["skills"][0]["name"] == "test.echo"
    assert len(runtime.runner.runs) == 0


def test_messages_for_same_session_execute_serially() -> None:
    provider = QueueProvider(
        RouteDecision(skill="test.concurrent"),
        ArgumentDecision(arguments={"text": "first"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        RouteDecision(skill="test.concurrent"),
        ArgumentDecision(arguments={"text": "second"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    skill = ConcurrentEchoSkill()
    runtime = make_runtime(provider, skill)

    async def send_both():
        return await asyncio.gather(
            runtime.handle_message("cli:shared", "First."),
            runtime.handle_message("cli:shared", "Second."),
        )

    responses = asyncio.run(send_both())

    assert [response.kind for response in responses] == ["COMPLETED", "COMPLETED"]
    assert skill.maximum_active == 1


def test_messages_for_different_sessions_can_execute_concurrently() -> None:
    provider = QueueProvider(
        RouteDecision(skill="test.concurrent"),
        ArgumentDecision(arguments={"text": "first"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
        RouteDecision(skill="test.concurrent"),
        ArgumentDecision(arguments={"text": "second"}),
        ValidationDecision(decision=ValidationOutcome.APPROVE),
    )
    skill = ConcurrentEchoSkill()
    runtime = make_runtime(provider, skill)

    async def send_both():
        return await asyncio.gather(
            runtime.handle_message("cli:first", "First."),
            runtime.handle_message("cli:second", "Second."),
        )

    responses = asyncio.run(send_both())

    assert [response.kind for response in responses] == ["COMPLETED", "COMPLETED"]
    assert skill.maximum_active == 2


def test_unix_socket_service_exposes_status_skills_and_chat() -> None:
    async def scenario() -> None:
        provider = QueueProvider()
        runtime = make_runtime(provider)
        socket = Path.cwd() / f"q-{uuid4().hex[:8]}.sock"
        service = RuntimeService(runtime, socket)
        await service.start()
        client = LocalRuntimeClient(socket)
        try:
            status = await client.request(GatewayRequest(command=GatewayCommand.STATUS))
            skills = await client.request(GatewayRequest(command=GatewayCommand.SKILLS))
            chat = await client.request(
                GatewayRequest(
                    command=GatewayCommand.CHAT,
                    session_key="cli:default",
                    text="Do something.",
                )
            )
        finally:
            await service.close()

        assert status.kind == "STATUS"
        assert status.data["runs"] == 0
        assert skills.data == {"skills": []}
        assert chat.kind == "UNSUPPORTED"
        assert not socket.exists()

    asyncio.run(scenario())


def test_service_refuses_to_replace_regular_file(tmp_path) -> None:
    async def scenario() -> None:
        provider = QueueProvider()
        runtime = make_runtime(provider)
        path = tmp_path / "not-a-socket"
        path.write_text("keep me")
        service = RuntimeService(runtime, path)

        try:
            await service.start()
        except RuntimeError as exc:
            assert "non-socket" in str(exc)
        else:
            raise AssertionError("service unexpectedly replaced a regular file")
        finally:
            await service.close()

        assert path.read_text() == "keep me"

    asyncio.run(scenario())
