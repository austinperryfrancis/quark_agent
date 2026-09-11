import asyncio

from pydantic import BaseModel, ConfigDict
import pytest

from quark.gateways import GatewayCommand, GatewayRequest, RecoveryAction
from quark.models import EventType, RunStatus, SideEffect, SkillCallStatus
from quark.persistence import SQLiteStateStore
from quark.runtime import QuarkRuntime, RunRecoveryError, RuntimeService, SkillRunner
from quark.skills import Skill, SkillContext, SkillRegistry


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class TextOutput(BaseModel):
    text: str


class RecordingWrite(Skill):
    name = "test.write"
    description = "Record a reviewed write."
    input_schema = TextInput
    output_schema = TextOutput
    side_effect = SideEffect.LOCAL_WRITE

    def __init__(self) -> None:
        self.executions: list[str] = []

    async def run(self, ctx: SkillContext, args: BaseModel) -> TextOutput:
        assert isinstance(args, TextInput)
        self.executions.append(args.text)
        return TextOutput(text=args.text)


class CountingRead(Skill):
    name = "test.read"
    description = "Return deterministic text."
    input_schema = TextInput
    output_schema = TextOutput
    side_effect = SideEffect.READ_ONLY

    def __init__(self) -> None:
        self.executions = 0

    async def run(self, ctx: SkillContext, args: BaseModel) -> TextOutput:
        assert isinstance(args, TextInput)
        self.executions += 1
        return TextOutput(text=args.text.upper())


class ParentSkill(Skill):
    name = "test.parent"
    description = "Read and then write text."
    input_schema = TextInput
    output_schema = TextOutput
    allowed_children = ("test.read", "test.write")
    side_effect = SideEffect.READ_ONLY

    async def run(self, ctx: SkillContext, args: BaseModel) -> TextOutput:
        assert isinstance(args, TextInput)
        read = await ctx.call_skill("test.read", {"text": args.text})
        return await ctx.call_skill("test.write", {"text": read.text})


def registry_with(*skills: Skill) -> SkillRegistry:
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return registry


def test_waiting_review_is_restored_and_can_complete_after_restart(tmp_path) -> None:
    database = tmp_path / "quark.db"
    first_store = SQLiteStateStore(database)
    first_skill = RecordingWrite()
    first = SkillRunner(registry_with(first_skill), state_store=first_store)
    waiting = asyncio.run(
        first.run("test.write", {"text": "hello"}, session_id="cli:default")
    )
    first_store.close()

    second_store = SQLiteStateStore(database)
    restored_skill = RecordingWrite()
    restored = SkillRunner(registry_with(restored_skill), state_store=second_store)
    completed = asyncio.run(restored.handle_response("cli:default", "approve"))

    assert restored.calls[waiting.id].status is SkillCallStatus.COMPLETED
    assert completed.id == waiting.id
    assert restored_skill.executions == ["hello"]
    assert restored.sessions["cli:default"].pending_interaction is None
    assert restored.runs[waiting.run_id].status is RunStatus.COMPLETED
    second_store.close()


def test_executing_call_is_interrupted_without_being_reexecuted(tmp_path) -> None:
    database = tmp_path / "quark.db"
    store = SQLiteStateStore(database)
    skill = RecordingWrite()
    runner = SkillRunner(registry_with(skill), state_store=store)
    call = asyncio.run(
        runner.run("test.write", {"text": "unsafe"}, session_id="cli:default")
    )
    pending = runner.sessions["cli:default"].pending_interaction
    assert pending is not None
    runner._clear_pending(runner.sessions["cli:default"], pending)
    call.transition(SkillCallStatus.APPROVED)
    call.transition(SkillCallStatus.EXECUTING)
    run = runner.runs[call.run_id]
    run.status = RunStatus.RUNNING
    runner._persist_run(run)
    store.close()

    reopened = SQLiteStateStore(database)
    restored_skill = RecordingWrite()
    restored = SkillRunner(registry_with(restored_skill), state_store=reopened)
    restored_call = restored.calls[call.id]

    assert restored_call.status is SkillCallStatus.INTERRUPTED
    assert restored_call.error.code == "EXECUTION_INTERRUPTED"
    assert restored_call.error.recoverable
    assert restored.runs[run.id].status is RunStatus.INTERRUPTED
    assert restored_skill.executions == []
    reopened.close()


def test_composite_resumes_after_child_review_without_repeating_completed_child(
    tmp_path,
) -> None:
    database = tmp_path / "quark.db"
    first_store = SQLiteStateStore(database)
    first_read = CountingRead()
    first_write = RecordingWrite()
    first = SkillRunner(
        registry_with(first_read, first_write, ParentSkill()),
        state_store=first_store,
    )
    parent = asyncio.run(
        first.run("test.parent", {"text": "hello"}, session_id="cli:default")
    )
    assert first_read.executions == 1
    assert parent.status is SkillCallStatus.AWAITING_REVIEW
    first_store.close()

    second_store = SQLiteStateStore(database)
    restored_read = CountingRead()
    restored_write = RecordingWrite()
    restored = SkillRunner(
        registry_with(restored_read, restored_write, ParentSkill()),
        state_store=second_store,
    )
    completed = asyncio.run(restored.handle_response("cli:default", "yes"))

    assert completed.id == parent.id
    assert completed.status is SkillCallStatus.COMPLETED
    assert restored_read.executions == 0
    assert restored_write.executions == ["HELLO"]
    assert len(restored.calls) == 3
    assert restored.runs[parent.run_id].status is RunStatus.COMPLETED
    second_store.close()


def test_cancelling_waiting_child_cancels_composite_run(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "quark.db")
    runner = SkillRunner(
        registry_with(CountingRead(), RecordingWrite(), ParentSkill()),
        state_store=store,
    )
    parent = asyncio.run(
        runner.run("test.parent", {"text": "hello"}, session_id="cli:default")
    )

    cancelled = asyncio.run(runner.handle_response("cli:default", "cancel"))

    assert cancelled.id == parent.id
    assert cancelled.status is SkillCallStatus.CANCELLED
    assert runner.runs[parent.run_id].status is RunStatus.CANCELLED
    assert runner.sessions["cli:default"].pending_interaction is None
    store.close()


def test_explicit_retry_executes_recoverable_interrupted_atomic_call(tmp_path) -> None:
    database = tmp_path / "quark.db"
    first_store = SQLiteStateStore(database)
    first = SkillRunner(registry_with(RecordingWrite()), state_store=first_store)
    call = asyncio.run(
        first.run("test.write", {"text": "retry me"}, session_id="cli:default")
    )
    pending = first.sessions["cli:default"].pending_interaction
    assert pending is not None
    first._clear_pending(first.sessions["cli:default"], pending)
    call.transition(SkillCallStatus.APPROVED)
    call.transition(SkillCallStatus.EXECUTING)
    first.runs[call.run_id].status = RunStatus.RUNNING
    first._persist_run(first.runs[call.run_id])
    first_store.close()

    reopened = SQLiteStateStore(database)
    skill = RecordingWrite()
    restored = SkillRunner(registry_with(skill), state_store=reopened)
    completed = asyncio.run(restored.retry_interrupted_run(call.run_id))

    assert completed.id == call.id
    assert completed.status is SkillCallStatus.COMPLETED
    assert skill.executions == ["retry me"]
    assert restored.runs[call.run_id].status is RunStatus.COMPLETED
    event_types = [event.type for event in reopened.list_events(call.run_id)]
    assert EventType.CALL_INTERRUPTED in event_types
    assert EventType.CALL_RETRY_REQUESTED in event_types
    reopened.close()


def test_service_commands_list_inspect_and_cancel_interrupted_run(tmp_path) -> None:
    database = tmp_path / "quark.db"
    first_store = SQLiteStateStore(database)
    first = SkillRunner(registry_with(RecordingWrite()), state_store=first_store)
    call = asyncio.run(
        first.run("test.write", {"text": "cancel me"}, session_id="cli:default")
    )
    pending = first.sessions["cli:default"].pending_interaction
    assert pending is not None
    first._clear_pending(first.sessions["cli:default"], pending)
    call.transition(SkillCallStatus.APPROVED)
    call.transition(SkillCallStatus.EXECUTING)
    first.runs[call.run_id].status = RunStatus.RUNNING
    first._persist_run(first.runs[call.run_id])
    first_store.close()

    reopened = SQLiteStateStore(database)
    registry = registry_with(RecordingWrite())
    runner = SkillRunner(registry, state_store=reopened)
    runtime = QuarkRuntime(registry, None, runner)  # Router is unused by admin commands.
    service = RuntimeService(runtime, tmp_path / "unused.sock")

    listed = asyncio.run(
        service._dispatch(GatewayRequest(command=GatewayCommand.RUNS))
    )
    inspected = asyncio.run(
        service._dispatch(
            GatewayRequest(command=GatewayCommand.RUN, run_id=call.run_id)
        )
    )
    cancelled = asyncio.run(
        service._dispatch(
            GatewayRequest(
                command=GatewayCommand.RECOVER,
                run_id=call.run_id,
                recovery_action=RecoveryAction.CANCEL,
            )
        )
    )

    assert listed.data["runs"][0]["status"] == RunStatus.INTERRUPTED.value
    assert inspected.data["skill_calls"][0]["status"] == SkillCallStatus.INTERRUPTED.value
    assert cancelled.kind == "RUN_CANCELLED"
    assert runner.runs[call.run_id].status is RunStatus.CANCELLED
    assert reopened.load_run(call.run_id).status is RunStatus.CANCELLED
    reopened.close()


def test_selected_interrupted_child_retries_and_resumes_composite(tmp_path) -> None:
    database = tmp_path / "quark.db"
    first_store = SQLiteStateStore(database)
    first_read = CountingRead()
    first = SkillRunner(
        registry_with(first_read, RecordingWrite(), ParentSkill()),
        state_store=first_store,
    )
    parent = asyncio.run(
        first.run("test.parent", {"text": "hello"}, session_id="cli:default")
    )
    pending = first.sessions["cli:default"].pending_interaction
    assert pending is not None and pending.skill_call_id is not None
    child = first.calls[pending.skill_call_id]
    first._clear_pending(first.sessions["cli:default"], pending)
    child.transition(SkillCallStatus.APPROVED)
    child.transition(SkillCallStatus.EXECUTING)
    parent.transition(SkillCallStatus.EXECUTING)
    first.runs[parent.run_id].status = RunStatus.RUNNING
    first._persist_run(first.runs[parent.run_id])
    first_store.close()

    reopened = SQLiteStateStore(database)
    restored_read = CountingRead()
    restored_write = RecordingWrite()
    restored = SkillRunner(
        registry_with(restored_read, restored_write, ParentSkill()),
        state_store=reopened,
    )

    with pytest.raises(RunRecoveryError, match="explicit interrupted child"):
        asyncio.run(restored.retry_interrupted_run(parent.run_id))

    completed = asyncio.run(
        restored.retry_interrupted_run(parent.run_id, skill_call_id=child.id)
    )

    assert completed.id == parent.id
    assert completed.status is SkillCallStatus.COMPLETED
    assert restored_read.executions == 0
    assert restored_write.executions == ["HELLO"]
    assert len(restored.calls) == 3
    assert restored.runs[parent.run_id].status is RunStatus.COMPLETED
    reopened.close()


def test_composite_replay_accepts_validated_child_argument_revision() -> None:
    read = CountingRead()
    write = RecordingWrite()
    runner = SkillRunner(registry_with(read, write, ParentSkill()))
    parent = asyncio.run(
        runner.run("test.parent", {"text": "original"}, session_id="cli:default")
    )
    pending = runner.sessions["cli:default"].pending_interaction
    assert pending is not None and pending.skill_call_id is not None
    child = runner.calls[pending.skill_call_id]
    child.revise({"text": "REVISED"}, source="user")
    child.transition(SkillCallStatus.SCHEMA_VALIDATING)
    child.transition(SkillCallStatus.AWAITING_REVIEW)

    completed = asyncio.run(runner.handle_response("cli:default", "approve"))

    assert completed.id == parent.id
    assert completed.status is SkillCallStatus.COMPLETED
    assert completed.result == TextOutput(text="REVISED")
    assert read.executions == 1
    assert write.executions == ["REVISED"]
