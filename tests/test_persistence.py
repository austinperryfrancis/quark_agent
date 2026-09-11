import asyncio

from pydantic import BaseModel, ConfigDict

from quark.models import (
    EventType,
    ExecutionEvent,
    InteractionType,
    PendingInteraction,
    Run,
    RunStatus,
    Session,
    SideEffect,
    SkillCall,
    SkillCallStatus,
)
from quark.persistence import SQLiteStateStore
from quark.runtime import SkillRunner
from quark.skills import Skill, SkillContext, SkillRegistry


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class TextOutput(BaseModel):
    text: str


class WriteSkill(Skill):
    name = "test.write"
    description = "Write test text."
    input_schema = TextInput
    output_schema = TextOutput
    side_effect = SideEffect.LOCAL_WRITE

    async def run(self, ctx: SkillContext, args: BaseModel) -> TextOutput:
        assert isinstance(args, TextInput)
        return TextOutput(text=args.text)


def test_core_objects_and_revision_history_round_trip(tmp_path) -> None:
    database = tmp_path / "quark.db"
    store = SQLiteStateStore(database)
    session = Session(session_key="cli:default")
    run = Run(session_id=session.id, original_request="Write text.")
    call = SkillCall(
        run_id=run.id, skill_name="test.write", arguments={"text": "first"}
    )
    call.transition(SkillCallStatus.PROPOSED)
    call.transition(SkillCallStatus.SCHEMA_VALIDATING)
    call.transition(SkillCallStatus.VALIDATING)
    call.transition(SkillCallStatus.AWAITING_REVIEW)
    call.revise({"text": "second"}, source="user")
    pending = PendingInteraction(
        type=InteractionType.REVIEW_CALL,
        run_id=run.id,
        skill_call_id=call.id,
        original_request=run.original_request,
        proposed_call=call,
    )
    session.active_run_id = run.id
    session.pending_interaction = pending

    store.save_runtime_state(
        session=session, run=run, calls=(call,), pending=pending
    )
    store.close()

    reopened = SQLiteStateStore(database)
    restored_call = reopened.load_skill_call(call.id)
    restored_session = reopened.load_session(session.id)
    restored_pending = reopened.load_pending_interaction(pending.id)

    assert restored_call.arguments == {"text": "second"}
    assert [revision.arguments for revision in restored_call.revisions] == [
        {"text": "first"},
        {"text": "second"},
    ]
    assert restored_session.pending_interaction.id == pending.id
    assert reopened.load_session_by_key("cli:default").id == session.id
    assert restored_pending.proposed_call.arguments == {"text": "second"}
    reopened.close()


def test_events_are_append_only_and_ordered() -> None:
    store = SQLiteStateStore()
    first = ExecutionEvent(type=EventType.RUN_CREATED, run_id="run_1")
    second = ExecutionEvent(
        type=EventType.CALL_PROPOSED,
        run_id="run_1",
        skill_call_id="call_1",
    )

    store.append_event(first)
    store.append_event(second)

    assert store.list_events("run_1") == (first, second)
    store.close()


def test_pending_interaction_can_be_deleted() -> None:
    store = SQLiteStateStore()
    pending = PendingInteraction(
        type=InteractionType.ASK_USER,
        run_id="run_1",
        original_request="Do it.",
        question="Which one?",
    )
    store.save_pending_interaction(pending)

    store.delete_pending_interaction(pending.id)

    assert store.load_pending_interaction(pending.id) is None
    store.close()


def test_authoritative_snapshot_removes_stale_pending_interaction() -> None:
    store = SQLiteStateStore()
    session = Session(session_key="cli:default")
    run = Run(session_id=session.id, original_request="Do it.")
    pending = PendingInteraction(
        type=InteractionType.ASK_USER,
        run_id=run.id,
        original_request=run.original_request,
        question="Which one?",
    )
    session.pending_interaction = pending
    store.save_runtime_state(
        session=session, run=run, calls=(), pending=pending
    )

    session.pending_interaction = None
    store.save_runtime_state(session=session, run=run, calls=(), pending=None)

    assert store.load_pending_interaction(pending.id) is None
    store.close()


def test_runner_persists_waiting_review_and_approved_completion(tmp_path) -> None:
    store = SQLiteStateStore(tmp_path / "runtime.db")
    registry = SkillRegistry()
    registry.register(WriteSkill())
    runner = SkillRunner(registry, state_store=store)

    call = asyncio.run(
        runner.run(
            "test.write",
            {"text": "persisted"},
            session_id="cli:default",
            original_request="Write persisted.",
        )
    )
    pending_id = runner.sessions["cli:default"].pending_interaction.id

    assert store.load_run(call.run_id).status is RunStatus.WAITING
    assert store.load_skill_call(call.id).status == call.status
    assert store.load_pending_interaction(pending_id) is not None

    completed = asyncio.run(runner.handle_response("cli:default", "yes"))

    assert store.load_run(call.run_id).status is RunStatus.COMPLETED
    assert store.load_skill_call(call.id).result == completed.result.model_dump()
    assert store.load_pending_interaction(pending_id) is None
    event_types = [event.type for event in store.list_events(call.run_id)]
    assert event_types == [
        EventType.RUN_CREATED,
        EventType.CALL_PROPOSED,
        EventType.CALL_REVIEW_REQUESTED,
        EventType.USER_RESPONSE_RECEIVED,
        EventType.CALL_APPROVED,
        EventType.CALL_STARTED,
        EventType.CALL_COMPLETED,
    ]
    store.close()
