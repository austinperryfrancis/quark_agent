"""The single conversation runtime shared by every gateway."""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic import BaseModel

from quark.gateways.models import GatewayResponse, RecoveryAction
from quark.inference import RootRouter
from quark.models import InteractionType, Run, RunStatus, SkillCall, SkillCallStatus
from quark.runtime.runner import (
    PendingInteractionNotFoundError,
    RunRecoveryError,
    SkillRunError,
    SkillRunner,
)
from quark.skills import SkillRegistry


class QuarkRuntime:
    """Coordinate routing, pending interactions, and Skill execution."""

    def __init__(
        self, registry: SkillRegistry, router: RootRouter, runner: SkillRunner
    ) -> None:
        self.registry = registry
        self.router = router
        self.runner = runner
        self._session_locks: dict[str, asyncio.Lock] = {}

    async def handle_message(self, session_key: str, text: str) -> GatewayResponse:
        async with self._session_lock(session_key):
            return await self._handle_message(session_key, text)

    async def _handle_message(self, session_key: str, text: str) -> GatewayResponse:
        normalized = text.strip().casefold().rstrip(".!?")
        if normalized in {
            "what skills do you have",
            "what skills are available",
            "what can you do",
            "show skills",
            "list skills",
        }:
            return self.skills()
        if normalized in {"status", "show status", "runtime status"}:
            return self.status()
        if normalized in {"show runs", "list runs"}:
            return self.list_runs()
        session = self.runner.sessions.get(session_key)
        if session and session.pending_interaction:
            try:
                call = await self.runner.handle_response(session_key, text)
            except PendingInteractionNotFoundError:
                call = None
            if call is not None:
                return self._render_call(call)

        decision = await self.router.route(text)
        if decision.unsupported:
            return GatewayResponse(
                ok=True,
                kind="UNSUPPORTED",
                message="Quark does not have an enabled Skill for that request.",
            )

        call = await self.runner.run_generated(
            decision.skill,
            text,
            session_id=session_key,
        )
        return self._render_call(call)

    def status(self) -> GatewayResponse:
        running = sum(run.status is RunStatus.RUNNING for run in self.runner.runs.values())
        waiting = sum(run.status is RunStatus.WAITING for run in self.runner.runs.values())
        interrupted = sum(
            run.status is RunStatus.INTERRUPTED for run in self.runner.runs.values()
        )
        return GatewayResponse(
            ok=True,
            kind="STATUS",
            message="Quark runtime is running.",
            data={
                "installed_skills": len(self.registry),
                "sessions": len(self.runner.sessions),
                "runs": len(self.runner.runs),
                "running_runs": running,
                "waiting_runs": waiting,
                "interrupted_runs": interrupted,
            },
        )

    def skills(self) -> GatewayResponse:
        skills = [
            {
                "name": skill.name,
                "description": skill.description,
                "top_level": skill.top_level,
                "side_effect": skill.side_effect.value,
                "review_policy": skill.review_policy.value,
                "allowed_children": list(skill.allowed_children),
            }
            for skill in self.registry
        ]
        return GatewayResponse(
            ok=True,
            kind="SKILLS",
            message=f"{len(skills)} Skills installed.",
            data={"skills": skills},
        )

    def list_runs(self) -> GatewayResponse:
        runs = sorted(
            self.runner.runs.values(), key=lambda run: run.created_at, reverse=True
        )
        return GatewayResponse(
            ok=True,
            kind="RUNS",
            message=f"{len(runs)} Runs persisted.",
            data={"runs": [self._run_summary(run) for run in runs]},
        )

    def inspect_run(self, run_id: str) -> GatewayResponse:
        run = self.runner.runs.get(run_id)
        if run is None:
            return GatewayResponse(
                ok=False,
                kind="RUN_NOT_FOUND",
                message=f"Run {run_id!r} was not found.",
            )
        calls = [
            call.model_dump(mode="json")
            for call in self.runner.calls.values()
            if call.run_id == run.id
        ]
        return GatewayResponse(
            ok=True,
            kind="RUN",
            message=f"Run {run.id} is {run.status.value}.",
            run_id=run.id,
            data={"run": run.model_dump(mode="json"), "skill_calls": calls},
        )

    async def recover_run(
        self,
        run_id: str,
        action: RecoveryAction,
        *,
        skill_call_id: str | None = None,
    ) -> GatewayResponse:
        run = self.runner.runs.get(run_id)
        if run is None:
            return GatewayResponse(
                ok=False,
                kind="RECOVERY_REJECTED",
                message=f"Run {run_id!r} was not found",
                run_id=run_id,
            )
        async with self._session_lock(run.session_id):
            return await self._recover_run(
                run_id, action, skill_call_id=skill_call_id
            )

    async def _recover_run(
        self,
        run_id: str,
        action: RecoveryAction,
        *,
        skill_call_id: str | None = None,
    ) -> GatewayResponse:
        try:
            if action is RecoveryAction.CANCEL:
                run = self.runner.cancel_interrupted_run(run_id)
                return GatewayResponse(
                    ok=True,
                    kind="RUN_CANCELLED",
                    message=f"Cancelled interrupted Run {run.id}.",
                    run_id=run.id,
                )
            call = await self.runner.retry_interrupted_run(
                run_id, skill_call_id=skill_call_id
            )
            return self._render_call(call)
        except RunRecoveryError as exc:
            return GatewayResponse(
                ok=False,
                kind="RECOVERY_REJECTED",
                message=str(exc),
                run_id=run_id,
            )
        except SkillRunError as exc:
            return self._render_call(exc.call)

    def _session_lock(self, session_key: str) -> asyncio.Lock:
        return self._session_locks.setdefault(session_key, asyncio.Lock())

    @staticmethod
    def _run_summary(run: Run) -> dict[str, Any]:
        return {
            "id": run.id,
            "session_id": run.session_id,
            "status": run.status.value,
            "original_request": run.original_request,
            "root_skill_call_id": run.root_skill_call_id,
            "created_at": run.created_at.isoformat(),
            "updated_at": run.updated_at.isoformat(),
        }

    def _render_call(self, call: SkillCall) -> GatewayResponse:
        run = self.runner.runs[call.run_id]
        if call.status is SkillCallStatus.COMPLETED:
            result = (
                call.result.model_dump(mode="json")
                if isinstance(call.result, BaseModel)
                else call.result
            )
            return GatewayResponse(
                ok=True,
                kind="COMPLETED",
                message=f"Completed {call.skill_name}.",
                run_id=run.id,
                skill_call_id=call.id,
                data={"result": result},
            )
        if call.status in {
            SkillCallStatus.AWAITING_INPUT,
            SkillCallStatus.AWAITING_REVIEW,
        }:
            pending = self.runner.sessions[run.session_id].pending_interaction
            kind = (
                "REVIEW_CALL"
                if pending and pending.type is InteractionType.REVIEW_CALL
                else "ASK_USER"
            )
            return GatewayResponse(
                ok=True,
                kind=kind,
                message=pending.question if pending and pending.question else "Input required.",
                run_id=run.id,
                skill_call_id=pending.skill_call_id if pending else call.id,
                data={
                    "skill": pending.proposed_call.skill_name if pending and pending.proposed_call else call.skill_name,
                    "arguments": pending.proposed_call.arguments if pending and pending.proposed_call else call.arguments,
                },
            )
        return GatewayResponse(
            ok=False,
            kind=call.status.value,
            message=call.error.message if call.error else f"Call {call.status.value.lower()}.",
            run_id=run.id,
            skill_call_id=call.id,
        )
