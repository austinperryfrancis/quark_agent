"""Deterministic lifecycle management for typed recursive Skill execution."""

from __future__ import annotations

from collections import Counter
import logging
from typing import Any

from pydantic import BaseModel, ValidationError

from quark.inference.models import (
    ArgumentDecision,
    InteractionRelationship,
    InteractionRelationshipDecision,
    Message,
    MessageRole,
    ValidationDecision,
    ValidationOutcome,
    InferenceTaskType,
)
from quark.inference.prompts import (
    build_classify_response_prompt,
    build_edit_arguments_prompt,
    build_generate_arguments_prompt,
    build_validate_call_prompt,
)
from quark.inference.provider import ModelProvider, ModelProviderError
from quark.models import (
    EventType,
    ExecutionEvent,
    InteractionType,
    PendingInteraction,
    ReviewPolicy,
    Run,
    RunStatus,
    Session,
    SideEffect,
    SkillCall,
    SkillCallStatus,
    SkillError,
)
from quark.models.core import utc_now
from quark.persistence import SQLiteStateStore
from quark.skills.base import Skill, SkillContext
from quark.skills.registry import SkillRegistry

logger = logging.getLogger(__name__)


class ChildSkillNotAllowedError(PermissionError):
    pass


class ExecutionLimitError(RuntimeError):
    pass


class PendingInteractionNotFoundError(LookupError):
    pass


class RunRecoveryError(RuntimeError):
    pass


class _ChildSkillWaiting(RuntimeError):
    def __init__(self, call: SkillCall) -> None:
        self.call = call
        super().__init__(f"Child Skill {call.skill_name!r} is waiting")


class _ChildSkillRejected(RuntimeError):
    def __init__(self, call: SkillCall) -> None:
        self.call = call
        super().__init__(f"Child Skill {call.skill_name!r} was rejected")


class SkillRunError(RuntimeError):
    """Raised after a SkillCall has been marked failed."""

    def __init__(self, call: SkillCall) -> None:
        self.call = call
        message = call.error.message if call.error else "Skill execution failed"
        super().__init__(message)


class _RunnerContext:
    def __init__(self, runner: SkillRunner, call: SkillCall) -> None:
        self._runner = runner
        self._call = call
        self.run_id = call.run_id
        self.call_id = call.id

    async def call_skill(
        self, skill_name: str, arguments: dict[str, Any]
    ) -> BaseModel:
        parent_skill = self._runner.registry.get(self._call.skill_name)
        if skill_name not in parent_skill.allowed_children:
            raise ChildSkillNotAllowedError(
                f"Skill {parent_skill.name!r} may not call {skill_name!r}"
            )
        replayed = self._runner._replay_child(self._call, skill_name, arguments)
        if replayed is not None:
            if replayed.status is SkillCallStatus.COMPLETED:
                child_skill = self._runner.registry.get(replayed.skill_name)
                return child_skill.output_schema.model_validate(replayed.result)
            if replayed.status in {
                SkillCallStatus.AWAITING_INPUT,
                SkillCallStatus.AWAITING_REVIEW,
            }:
                raise _ChildSkillWaiting(replayed)
            if replayed.status is SkillCallStatus.REJECTED:
                raise _ChildSkillRejected(replayed)
            raise SkillRunError(replayed)
        if self._runner._child_counts[self._call.id] >= parent_skill.max_child_calls:
            raise ExecutionLimitError(
                f"Skill {parent_skill.name!r} exceeded its child-call limit"
            )
        self._runner._child_counts[self._call.id] += 1
        child = await self._runner._execute(
            skill_name=skill_name,
            arguments=arguments,
            run_id=self._call.run_id,
            parent_call_id=self._call.id,
            depth=self._call.depth + 1,
            proposed_by=self._call.skill_name,
        )
        if child.status in {
            SkillCallStatus.AWAITING_INPUT,
            SkillCallStatus.AWAITING_REVIEW,
        }:
            raise _ChildSkillWaiting(child)
        if child.status is SkillCallStatus.REJECTED:
            raise _ChildSkillRejected(child)
        return child.result

    async def infer(
        self,
        task: InferenceTaskType,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
    ) -> BaseModel:
        if self._runner.provider is None:
            raise RuntimeError(f"{task.value} requires a model provider")
        return await self._runner.provider.generate_structured(messages, schema)


class SkillRunner:
    """Execute every call through one schema, validation, and review path."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        provider: ModelProvider | None = None,
        state_store: SQLiteStateStore | None = None,
        max_depth: int = 8,
        max_total_calls: int = 100,
        max_validation_revisions: int = 2,
    ) -> None:
        if max_depth < 0 or max_total_calls < 1 or max_validation_revisions < 0:
            raise ValueError("Invalid execution limits")
        self.registry = registry
        self.provider = provider
        self.state_store = state_store
        self.max_depth = max_depth
        self.max_total_calls = max_total_calls
        self.max_validation_revisions = max_validation_revisions
        self.calls: dict[str, SkillCall] = {}
        self.runs: dict[str, Run] = {}
        self.sessions: dict[str, Session] = {}
        self.pending_interactions: dict[str, PendingInteraction] = {}
        self._run_call_counts: Counter[str] = Counter()
        self._child_counts: Counter[str] = Counter()
        self._child_replay_positions: Counter[str] = Counter()
        self._replaying_calls: set[str] = set()
        if self.state_store is not None:
            self.restore_state()

    def restore_state(self) -> None:
        """Hydrate persisted state and quarantine calls interrupted by a crash."""
        if self.state_store is None:
            return
        self.sessions = {
            session.session_key: session for session in self.state_store.list_sessions()
        }
        self.runs = {run.id: run for run in self.state_store.list_runs()}
        self.calls = {}
        self.pending_interactions = {}
        self._run_call_counts.clear()
        self._child_counts.clear()
        for run in self.runs.values():
            run_calls = self.state_store.list_run_calls(run.id)
            interrupted = False
            for call in run_calls:
                self.calls[call.id] = call
                self._run_call_counts[run.id] += 1
                if call.parent_call_id is not None:
                    self._child_counts[call.parent_call_id] += 1
                if call.status in {
                    SkillCallStatus.PROPOSED,
                    SkillCallStatus.SCHEMA_VALIDATING,
                    SkillCallStatus.VALIDATING,
                    SkillCallStatus.REVISED,
                    SkillCallStatus.APPROVED,
                    SkillCallStatus.EXECUTING,
                    SkillCallStatus.VALIDATING_RESULT,
                }:
                    call.error = SkillError(
                        code="EXECUTION_INTERRUPTED",
                        message="Skill execution was interrupted by a runtime restart.",
                        recoverable=True,
                    )
                    call.transition(SkillCallStatus.INTERRUPTED)
                    self._record_event(EventType.CALL_INTERRUPTED, run, call)
                    interrupted = True
            session = self.sessions.get(run.session_id)
            if session and session.pending_interaction:
                pending = session.pending_interaction
                self.pending_interactions[pending.id] = pending
            if interrupted or run.status is RunStatus.RUNNING:
                run.status = RunStatus.INTERRUPTED
                run.updated_at = utc_now()
                run.completed_at = None
                self._record_event(EventType.RUN_INTERRUPTED, run)
            if session is not None:
                self._persist_run(run)
        logger.info(
            "runtime state restored sessions=%d runs=%d calls=%d pending=%d",
            len(self.sessions),
            len(self.runs),
            len(self.calls),
            len(self.pending_interactions),
        )

    async def run_generated(
        self,
        skill_name: str,
        original_request: str,
        *,
        session_id: str = "local",
    ) -> SkillCall:
        """Generate a root call's arguments, then use the normal lifecycle."""
        if self.provider is None:
            raise RuntimeError("Argument generation requires a model provider")
        if not original_request.strip():
            raise ValueError("original_request cannot be empty")
        run = self._start_run(session_id, original_request)
        try:
            skill = self.registry.get(skill_name)
            generated = await self.provider.generate_structured(
                (
                    Message(
                        role=MessageRole.USER,
                        content=build_generate_arguments_prompt(original_request, skill),
                    ),
                ),
                ArgumentDecision,
            )
            call = await self._execute(
                skill_name=skill_name,
                arguments=generated.arguments,
                run_id=run.id,
                parent_call_id=None,
                depth=0,
                proposed_by="model",
            )
        except SkillRunError as exc:
            self._fail_run(run, exc.call if exc.call.depth == 0 else None)
            raise
        except Exception:
            self._fail_run(run)
            raise
        self._finalize_run(run, call)
        return call

    async def run(
        self,
        skill_name: str,
        arguments: dict[str, Any],
        *,
        session_id: str = "local",
        original_request: str = "",
        proposed_by: str = "runtime",
    ) -> SkillCall:
        run = self._start_run(session_id, original_request)
        try:
            call = await self._execute(
                skill_name=skill_name,
                arguments=arguments,
                run_id=run.id,
                parent_call_id=None,
                depth=0,
                proposed_by=proposed_by,
            )
        except SkillRunError as exc:
            self._fail_run(run, exc.call if exc.call.depth == 0 else None)
            raise
        except Exception:
            self._fail_run(run)
            raise
        self._finalize_run(run, call)
        return call

    async def handle_response(
        self, session_id: str, message: str
    ) -> SkillCall | None:
        try:
            return await self._apply_response(session_id, message)
        except SkillRunError as exc:
            run = self.runs[exc.call.run_id]
            root = exc.call if exc.call.depth == 0 else None
            self._fail_run(run, root)
            raise

    async def retry_interrupted_run(
        self, run_id: str, *, skill_call_id: str | None = None
    ) -> SkillCall:
        """Retry one explicit interrupted atomic call, then resume its parents."""
        run = self.runs.get(run_id)
        if run is None:
            raise RunRecoveryError(f"Run {run_id!r} was not found")
        if run.status is not RunStatus.INTERRUPTED:
            raise RunRecoveryError("Only an interrupted Run can be retried")
        root = self._root_call(run)
        root_skill = self.registry.get(root.skill_name)
        if skill_call_id is None and root_skill.allowed_children:
            raise RunRecoveryError(
                "Composite recovery requires an explicit interrupted child SkillCall"
            )
        target = self.calls.get(skill_call_id) if skill_call_id else root
        if target is None or target.run_id != run.id:
            raise RunRecoveryError("Recovery SkillCall was not found in this Run")
        interrupted_atomic_calls = [
            call
            for call in self.calls.values()
            if call.run_id == run.id
            and call.status is SkillCallStatus.INTERRUPTED
            and not self.registry.get(call.skill_name).allowed_children
        ]
        if len(interrupted_atomic_calls) != 1 or target not in interrupted_atomic_calls:
            raise RunRecoveryError(
                "Run does not have one unambiguous interrupted atomic SkillCall"
            )
        if target.status is not SkillCallStatus.INTERRUPTED:
            raise RunRecoveryError("Recovery SkillCall is not interrupted")
        if not target.error or not target.error.recoverable:
            raise RunRecoveryError("The interrupted SkillCall is not recoverable")
        skill = self.registry.get(target.skill_name)
        if skill.allowed_children:
            raise RunRecoveryError(
                "The selected recovery SkillCall must be atomic"
            )
        run.status = RunStatus.RUNNING
        run.updated_at = utc_now()
        run.completed_at = None
        target.error = None
        target.transition(SkillCallStatus.EXECUTING)
        self._record_event(EventType.CALL_RETRY_REQUESTED, run, target)
        self._persist_run(run)
        try:
            completed = await self._perform_skill(
                target, skill, skill.input_schema.model_validate(target.arguments)
            )
            if completed.parent_call_id is not None:
                completed = await self._resume_parent_chain(completed)
        except SkillRunError:
            self._fail_run(run, root)
            raise
        self._finalize_run(run, completed)
        return completed

    def cancel_interrupted_run(self, run_id: str) -> Run:
        run = self.runs.get(run_id)
        if run is None:
            raise RunRecoveryError(f"Run {run_id!r} was not found")
        if run.status is not RunStatus.INTERRUPTED:
            raise RunRecoveryError("Only an interrupted Run can be cancelled")
        session = self.sessions[run.session_id]
        if session.pending_interaction is not None:
            self._clear_pending(session, session.pending_interaction)
        for call in self.calls.values():
            if call.run_id == run.id and call.status in {
                SkillCallStatus.INTERRUPTED,
                SkillCallStatus.AWAITING_INPUT,
                SkillCallStatus.AWAITING_REVIEW,
            }:
                call.review_status = "CANCELLED"
                call.transition(SkillCallStatus.CANCELLED)
                self._record_event(EventType.CALL_CANCELLED, run, call)
        run.status = RunStatus.CANCELLED
        run.updated_at = utc_now()
        run.completed_at = run.updated_at
        self._persist_run(run)
        return run

    def _root_call(self, run: Run) -> SkillCall:
        if run.root_skill_call_id and run.root_skill_call_id in self.calls:
            return self.calls[run.root_skill_call_id]
        roots = [
            call
            for call in self.calls.values()
            if call.run_id == run.id and call.parent_call_id is None
        ]
        if len(roots) != 1:
            raise RunRecoveryError("Run does not have exactly one root SkillCall")
        return roots[0]

    async def _apply_response(
        self, session_id: str, message: str
    ) -> SkillCall | None:
        """Apply a user response to the current pending SkillCall."""
        session = self.sessions.get(session_id)
        pending = session.pending_interaction if session else None
        if pending is None or pending.skill_call_id is None:
            raise PendingInteractionNotFoundError(
                f"Session {session_id!r} has no pending interaction"
            )
        if not message.strip():
            raise ValueError("message cannot be empty")

        call = self.calls[pending.skill_call_id]
        self._record_event(
            EventType.USER_RESPONSE_RECEIVED,
            self.runs[call.run_id],
            call,
            {"relationship_pending": pending.type.value},
        )
        relationship = await self._classify_response(pending, message)
        if relationship is InteractionRelationship.NEW_REQUEST:
            return None
        if relationship is InteractionRelationship.APPROVAL:
            if pending.type is not InteractionType.REVIEW_CALL:
                return await self._revise_from_user(call, pending, message)
            self._clear_pending(session, pending)
            call.review_status = "APPROVED"
            call.transition(SkillCallStatus.APPROVED)
            self._record_event(EventType.CALL_APPROVED, self.runs[call.run_id], call)
            self._persist_run(self.runs[call.run_id])
            skill = self.registry.get(call.skill_name)
            call = await self._perform_skill(
                call, skill, skill.input_schema.model_validate(call.arguments)
            )
        elif relationship is InteractionRelationship.REJECTION:
            self._clear_pending(session, pending)
            call.review_status = "REJECTED"
            call.transition(SkillCallStatus.REJECTED)
            self._record_event(EventType.CALL_REJECTED, self.runs[call.run_id], call)
        elif relationship is InteractionRelationship.CANCELLATION:
            self._clear_pending(session, pending)
            call.review_status = "CANCELLED"
            call.transition(SkillCallStatus.CANCELLED)
            self._record_event(EventType.CALL_CANCELLED, self.runs[call.run_id], call)
        else:
            call = await self._revise_from_user(call, pending, message)

        if call.status is SkillCallStatus.COMPLETED and call.parent_call_id:
            call = await self._resume_parent_chain(call)
        elif call.parent_call_id and call.status in {
            SkillCallStatus.CANCELLED,
            SkillCallStatus.REJECTED,
        }:
            call = self._terminate_parent_chain(call)

        self._finalize_run(self.runs[call.run_id], call)
        return call

    async def _resume_parent_chain(self, child: SkillCall) -> SkillCall:
        """Replay composite control flow while reusing persisted child results."""
        current = child
        while current.parent_call_id is not None:
            parent = self.calls[current.parent_call_id]
            skill = self.registry.get(parent.skill_name)
            self._child_replay_positions[parent.id] = 0
            self._replaying_calls.add(parent.id)
            parent.error = None
            parent.transition(SkillCallStatus.EXECUTING)
            parent.validation_status = "CHILD_RESUMED"
            parent.validation_question = None
            try:
                current = await self._perform_skill(
                    parent, skill, skill.input_schema.model_validate(parent.arguments)
                )
            finally:
                self._replaying_calls.discard(parent.id)
            if current.status is not SkillCallStatus.COMPLETED:
                break
        return current

    def _terminate_parent_chain(self, child: SkillCall) -> SkillCall:
        current = child
        while current.parent_call_id is not None:
            parent = self.calls[current.parent_call_id]
            if child.status is SkillCallStatus.CANCELLED:
                parent.review_status = "CANCELLED"
                parent.transition(SkillCallStatus.CANCELLED)
                self._record_event(
                    EventType.CALL_CANCELLED, self.runs[parent.run_id], parent
                )
            else:
                parent.error = SkillError(
                    code="CHILD_SKILL_REJECTED",
                    message=f"Child Skill {current.skill_name!r} was rejected.",
                    details={"child_call_id": current.id},
                )
                parent.transition(SkillCallStatus.FAILED)
                self._record_event(EventType.CALL_FAILED, self.runs[parent.run_id], parent)
            current = parent
        return current

    def _replay_child(
        self, parent: SkillCall, skill_name: str, arguments: dict[str, Any]
    ) -> SkillCall | None:
        if parent.id not in self._replaying_calls:
            return None
        children = [
            call for call in self.calls.values() if call.parent_call_id == parent.id
        ]
        position = self._child_replay_positions[parent.id]
        if position >= len(children):
            return None
        child = children[position]
        if child.skill_name != skill_name:
            raise RuntimeError(
                "Composite Skill changed its persisted child-call sequence during resume"
            )
        self._child_replay_positions[parent.id] += 1
        return child

    async def _classify_response(
        self, pending: PendingInteraction, message: str
    ) -> InteractionRelationship:
        normalized = message.strip().casefold().rstrip(".!?")
        if normalized in {"yes", "y", "approve", "approved", "ok", "okay"}:
            return InteractionRelationship.APPROVAL
        if normalized in {"no", "reject", "rejected"}:
            return InteractionRelationship.REJECTION
        if normalized in {"cancel", "stop", "never mind", "nevermind"}:
            return InteractionRelationship.CANCELLATION
        if self.provider is None:
            return (
                InteractionRelationship.ANSWER
                if pending.type is InteractionType.ASK_USER
                else InteractionRelationship.EDIT
            )
        decision = await self.provider.generate_structured(
            (
                Message(
                    role=MessageRole.USER,
                    content=build_classify_response_prompt(pending, message),
                ),
            ),
            InteractionRelationshipDecision,
        )
        return decision.relationship

    async def _revise_from_user(
        self,
        call: SkillCall,
        pending: PendingInteraction,
        feedback: str,
    ) -> SkillCall:
        if self.provider is None:
            raise RuntimeError("Natural-language call revision requires a model provider")
        skill = self.registry.get(call.skill_name)
        revised = await self.provider.generate_structured(
            (
                Message(
                    role=MessageRole.USER,
                    content=build_edit_arguments_prompt(call, skill, feedback),
                ),
            ),
            ArgumentDecision,
        )
        session = self.sessions[self.runs[call.run_id].session_id]
        self._clear_pending(session, pending)
        try:
            call.revise(revised.arguments, source="user")
            self._record_event(EventType.CALL_EDITED, self.runs[call.run_id], call)
            call.transition(SkillCallStatus.SCHEMA_VALIDATING)
            parsed_args = self._parse_arguments(call, skill, revised=True)
            call.validation_status = "SCHEMA_VALID"
            parsed_args = await self._validate_call(call, skill, parsed_args)
            if parsed_args is None:
                return call
            return await self._perform_skill(call, skill, parsed_args)
        except SkillRunError:
            raise
        except Exception as exc:
            self._fail_call(call, exc)

    def _start_run(self, session_id: str, original_request: str) -> Run:
        session = self.sessions.setdefault(
            session_id, Session(id=session_id, session_key=session_id)
        )
        run = Run(session_id=session_id, original_request=original_request)
        run.status = RunStatus.RUNNING
        run.updated_at = utc_now()
        self.runs[run.id] = run
        session.active_run_id = run.id
        session.recent_user_request = original_request
        session.updated_at = utc_now()
        self._persist_run(run)
        self._record_event(EventType.RUN_CREATED, run)
        return run

    def _fail_run(self, run: Run, root_call: SkillCall | None = None) -> None:
        run.root_skill_call_id = root_call.id if root_call else None
        run.status = RunStatus.FAILED
        run.updated_at = utc_now()
        run.completed_at = run.updated_at
        self._persist_run(run)

    def _finalize_run(self, run: Run, call: SkillCall) -> None:
        if call.depth > 0:
            run.updated_at = utc_now()
            run.status = RunStatus.WAITING
            run.completed_at = None
            self._persist_run(run)
            return
        if call.depth == 0:
            run.root_skill_call_id = call.id
        run.updated_at = utc_now()
        if call.status in {
            SkillCallStatus.AWAITING_INPUT,
            SkillCallStatus.AWAITING_REVIEW,
        }:
            run.status = RunStatus.WAITING
            run.completed_at = None
            self._persist_run(run)
            return
        if call.status is SkillCallStatus.CANCELLED:
            run.status = RunStatus.CANCELLED
        elif call.status is SkillCallStatus.COMPLETED:
            run.status = RunStatus.COMPLETED
        else:
            run.status = RunStatus.FAILED
        run.completed_at = run.updated_at
        self._persist_run(run)

    async def _execute(
        self,
        *,
        skill_name: str,
        arguments: dict[str, Any],
        run_id: str,
        parent_call_id: str | None,
        depth: int,
        proposed_by: str,
    ) -> SkillCall:
        if depth > self.max_depth:
            raise ExecutionLimitError(f"Maximum Skill depth {self.max_depth} exceeded")
        if self._run_call_counts[run_id] >= self.max_total_calls:
            raise ExecutionLimitError(
                f"Run exceeded its {self.max_total_calls}-call limit"
            )
        skill = self.registry.get(skill_name)
        call = SkillCall(
            run_id=run_id,
            skill_name=skill_name,
            arguments=arguments,
            parent_call_id=parent_call_id,
            depth=depth,
            proposed_by=proposed_by,
        )
        self.calls[call.id] = call
        self._run_call_counts[run_id] += 1
        try:
            call.transition(SkillCallStatus.PROPOSED)
            self._record_event(EventType.CALL_PROPOSED, self.runs[run_id], call)
            self._persist_run(self.runs[run_id])
            call.transition(SkillCallStatus.SCHEMA_VALIDATING)
            parsed_args = self._parse_arguments(call, skill)
            call.validation_status = "SCHEMA_VALID"
            parsed_args = await self._validate_call(call, skill, parsed_args)
            if parsed_args is None:
                return call
            return await self._perform_skill(call, skill, parsed_args)
        except SkillRunError:
            raise
        except Exception as exc:
            self._fail_call(call, exc)

    def _parse_arguments(
        self, call: SkillCall, skill: Skill, *, revised: bool = False
    ) -> BaseModel:
        try:
            return skill.input_schema.model_validate(call.arguments)
        except ValidationError as exc:
            call.error = SkillError(
                code="INPUT_SCHEMA_VALIDATION_FAILED",
                message=(
                    "Revised arguments did not match the Skill input schema."
                    if revised
                    else "Skill arguments did not match its declared input schema."
                ),
                details={"errors": exc.errors(include_url=False)},
            )
            call.transition(SkillCallStatus.FAILED)
            self._record_event(EventType.CALL_FAILED, self.runs[call.run_id], call)
            self._persist_run(self.runs[call.run_id])
            raise SkillRunError(call) from exc

    async def _validate_call(
        self, call: SkillCall, skill: Skill, parsed_args: BaseModel
    ) -> BaseModel | None:
        if self.provider is None:
            return await self._after_validation_approval(call, skill, parsed_args)
        revisions = 0
        call.transition(SkillCallStatus.VALIDATING)
        while True:
            request = self.runs[call.run_id].original_request
            decision = await self.provider.generate_structured(
                (
                    Message(
                        role=MessageRole.USER,
                        content=build_validate_call_prompt(request, skill, call),
                    ),
                ),
                ValidationDecision,
            )
            call.validation_status = decision.decision.value
            call.validation_reason = decision.reason
            call.validation_question = decision.question
            if decision.decision is ValidationOutcome.APPROVE:
                self._record_event(EventType.CALL_VALIDATED, self.runs[call.run_id], call)
                return await self._after_validation_approval(call, skill, parsed_args)
            if decision.decision is ValidationOutcome.ASK_USER:
                call.transition(SkillCallStatus.AWAITING_INPUT)
                self._set_pending(call, InteractionType.ASK_USER)
                return None
            if decision.decision is ValidationOutcome.REJECT:
                call.transition(SkillCallStatus.REJECTED)
                self._record_event(EventType.CALL_REJECTED, self.runs[call.run_id], call)
                return None
            if revisions >= self.max_validation_revisions:
                call.validation_status = "REVISION_LIMIT_REACHED"
                call.validation_question = "Please provide corrected arguments."
                call.transition(SkillCallStatus.AWAITING_INPUT)
                self._set_pending(call, InteractionType.ASK_USER)
                return None
            call.revise(decision.revised_arguments or {}, source="validator")
            self._record_event(EventType.CALL_EDITED, self.runs[call.run_id], call)
            revisions += 1
            call.transition(SkillCallStatus.SCHEMA_VALIDATING)
            parsed_args = self._parse_arguments(call, skill, revised=True)
            call.validation_status = "SCHEMA_VALID"
            call.transition(SkillCallStatus.VALIDATING)

    async def _after_validation_approval(
        self, call: SkillCall, skill: Skill, parsed_args: BaseModel
    ) -> BaseModel | None:
        if self._requires_review(skill):
            call.transition(SkillCallStatus.AWAITING_REVIEW)
            call.review_status = "PENDING"
            self._set_pending(call, InteractionType.REVIEW_CALL)
            return None
        call.transition(SkillCallStatus.EXECUTING)
        return parsed_args

    @staticmethod
    def _requires_review(skill: Skill) -> bool:
        if skill.review_policy is ReviewPolicy.ALWAYS:
            return True
        if skill.review_policy is ReviewPolicy.NEVER:
            return False
        if skill.review_policy is ReviewPolicy.RISK_BASED:
            return skill.side_effect is SideEffect.DESTRUCTIVE
        return skill.side_effect is not SideEffect.READ_ONLY

    def _set_pending(self, call: SkillCall, interaction_type: InteractionType) -> None:
        run = self.runs[call.run_id]
        session = self.sessions[run.session_id]
        question = call.validation_question
        if interaction_type is InteractionType.REVIEW_CALL:
            question = f"Approve {call.skill_name} with the proposed arguments?"
        pending = PendingInteraction(
            type=interaction_type,
            run_id=run.id,
            skill_call_id=call.id,
            original_request=run.original_request,
            question=question,
            proposed_call=call.model_copy(deep=True),
        )
        self.pending_interactions[pending.id] = pending
        session.pending_interaction = pending
        session.updated_at = utc_now()
        event_type = (
            EventType.CALL_REVIEW_REQUESTED
            if interaction_type is InteractionType.REVIEW_CALL
            else EventType.USER_QUESTION_REQUESTED
        )
        self._record_event(event_type, run, call)
        self._persist_run(run)

    def _clear_pending(
        self, session: Session, pending: PendingInteraction
    ) -> None:
        self.pending_interactions.pop(pending.id, None)
        if self.state_store is not None:
            self.state_store.delete_pending_interaction(pending.id)
        session.pending_interaction = None
        session.updated_at = utc_now()

    async def _perform_skill(
        self, call: SkillCall, skill: Skill, parsed_args: BaseModel
    ) -> SkillCall:
        try:
            if call.status is SkillCallStatus.APPROVED:
                call.transition(SkillCallStatus.EXECUTING)
            self._persist_run(self.runs[call.run_id])
            self._record_event(EventType.CALL_STARTED, self.runs[call.run_id], call)
            raw_result = await skill.run(_RunnerContext(self, call), parsed_args)
            try:
                parsed_result = skill.output_schema.model_validate(raw_result)
            except ValidationError as exc:
                call.error = SkillError(
                    code="OUTPUT_SCHEMA_VALIDATION_FAILED",
                    message="Skill result did not match its declared output schema.",
                    details={"errors": exc.errors(include_url=False)},
                )
                call.transition(SkillCallStatus.FAILED)
                self._record_event(EventType.CALL_FAILED, self.runs[call.run_id], call)
                self._persist_run(self.runs[call.run_id])
                raise SkillRunError(call) from exc
            call.result = parsed_result
            call.transition(SkillCallStatus.COMPLETED)
            self._persist_run(self.runs[call.run_id])
            self._record_event(EventType.CALL_COMPLETED, self.runs[call.run_id], call)
            return call
        except SkillRunError as exc:
            if exc.call.id == call.id:
                raise
            call.error = SkillError(
                code="CHILD_SKILL_FAILED",
                message=f"Child Skill {exc.call.skill_name!r} failed.",
                recoverable=bool(exc.call.error and exc.call.error.recoverable),
                details={"child_call_id": exc.call.id},
            )
            call.transition(SkillCallStatus.FAILED)
            self._record_event(EventType.CALL_FAILED, self.runs[call.run_id], call)
            self._persist_run(self.runs[call.run_id])
            raise SkillRunError(call) from exc
        except _ChildSkillWaiting as exc:
            call.validation_status = "CHILD_WAITING"
            call.validation_question = exc.call.validation_question
            call.transition(exc.call.status)
            return call
        except _ChildSkillRejected as exc:
            call.error = SkillError(
                code="CHILD_SKILL_REJECTED",
                message=str(exc),
                details={"child_call_id": exc.call.id},
            )
            call.transition(SkillCallStatus.FAILED)
            self._record_event(EventType.CALL_FAILED, self.runs[call.run_id], call)
            self._persist_run(self.runs[call.run_id])
            raise SkillRunError(call) from exc
        except Exception as exc:
            self._fail_call(call, exc)

    def _fail_call(self, call: SkillCall, exc: Exception) -> None:
        call.error = self._as_skill_error(exc)
        call.transition(SkillCallStatus.FAILED)
        self._record_event(EventType.CALL_FAILED, self.runs[call.run_id], call)
        self._persist_run(self.runs[call.run_id])
        raise SkillRunError(call) from exc

    def _persist_run(self, run: Run) -> None:
        if self.state_store is None:
            return
        session = self.sessions[run.session_id]
        calls = tuple(call for call in self.calls.values() if call.run_id == run.id)
        self.state_store.save_runtime_state(
            session=session,
            run=run,
            calls=calls,
            pending=session.pending_interaction,
        )

    def _record_event(
        self,
        event_type: EventType,
        run: Run,
        call: SkillCall | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        logger.info(
            "%s run=%s call=%s",
            event_type.value,
            run.id,
            call.id if call else "-",
        )
        if self.state_store is None:
            return
        self.state_store.append_event(
            ExecutionEvent(
                type=event_type,
                run_id=run.id,
                skill_call_id=call.id if call else None,
                payload=payload or {},
            )
        )

    @staticmethod
    def _as_skill_error(exc: Exception) -> SkillError:
        if isinstance(exc, ModelProviderError):
            return SkillError(
                code=exc.code,
                message=str(exc),
                recoverable=exc.recoverable,
                details=exc.details,
            )
        if isinstance(exc, ChildSkillNotAllowedError):
            return SkillError(code="CHILD_SKILL_NOT_ALLOWED", message=str(exc))
        if isinstance(exc, ExecutionLimitError):
            return SkillError(code="EXECUTION_LIMIT_EXCEEDED", message=str(exc))
        return SkillError(
            code="SKILL_EXECUTION_FAILED",
            message=str(exc) or type(exc).__name__,
            details={"exception_type": type(exc).__name__},
        )
