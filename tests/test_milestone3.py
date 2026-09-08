from __future__ import annotations

from pathlib import Path
from threading import Event
from time import sleep

import pytest

from quark.capabilities.loader import load_manifest, load_skills
from quark.capabilities.registry import CapabilityError, CapabilityRegistry
from quark.capabilities.schemas import CapabilityMetadata, ExecutionLane, RiskClass
from quark.config import PermissionsConfig
from quark.core.compiler import (
    IRStep,
    IRValidationError,
    QuarkIR,
    compile_procedure,
    compile_user_goal,
)
from quark.core.permissions import authorize
from quark.core.scheduler import ExecutionResult, Scheduler
from quark.state.database import StateDatabase
from quark.state.models import GoalStatus, StepStatus

ROOT = Path(__file__).parents[1]


def test_skill_manifests_load_typed_metadata_and_all_mvp_capabilities() -> None:
    registry = load_skills(ROOT / "skills")

    assert "obsidian.read_note" in registry.names()
    assert "text.classify" in registry.names()
    assert registry.get("obsidian.read_note").risk is RiskClass.READ
    assert registry.get("obsidian.generate_tags").lane is ExecutionLane.SMALL_MODEL
    assert registry.get("obsidian.generate_tags").model_required


def test_duplicate_capabilities_are_rejected() -> None:
    registry = CapabilityRegistry()
    capability = CapabilityMetadata(name="duplicate")
    registry.register(capability)

    with pytest.raises(CapabilityError, match="duplicate capability"):
        registry.register(capability)


def test_manifest_validation_rejects_malformed_skill(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text("skill:\n  name: broken\n  version: 0.1.0\n")

    with pytest.raises(CapabilityError, match="capabilities"):
        load_manifest(manifest)


def test_permission_policy_requires_write_approval() -> None:
    write = CapabilityMetadata(name="write", risk=RiskClass.WRITE)
    policy = PermissionsConfig(allow_write=False)

    denied = authorize(write, policy)
    allowed = authorize(write, policy, approved=True)

    assert not denied.allowed and denied.requires_approval
    assert allowed.allowed and not allowed.requires_approval


def test_ir_compiler_loads_template_and_rejects_invalid_graph() -> None:
    registry = load_skills(ROOT / "skills")
    ir = compile_procedure(
        ROOT / "procedures" / "organize_note.yaml",
        target="Inbox/Meeting Note.md",
        registry=registry,
    )

    assert ir.goal_type == "obsidian.organize_note"
    assert ir.target == "Inbox/Meeting Note.md"
    assert ir.steps[-1].operation == "obsidian.apply_changes"

    invalid = QuarkIR(
        "broken",
        None,
        (
            # A dependency cycle should be rejected before execution.
            IRStep("a", "x", ("b",)),
            IRStep("b", "x", ("a",)),
        ),
    )
    with pytest.raises(IRValidationError, match="cycle"):
        invalid.validate()


def test_compile_user_goal_supports_only_known_mvp_templates() -> None:
    assert compile_user_goal("organize Inbox/Note.md").target == "Inbox/Note.md"
    assert compile_user_goal("organize-inbox").target is None
    with pytest.raises(IRValidationError, match="unsupported goal"):
        compile_user_goal("do anything")


def test_scheduler_runs_dependencies_and_resumes(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilityMetadata(name="first"))
    registry.register(CapabilityMetadata(name="second"))
    path = tmp_path / "state.db"
    calls: list[str] = []
    with StateDatabase(path) as state:
        goal = state.create_goal("test.scheduler")
        first = state.create_step(goal, "first", "first")
        state.create_step(goal, "second", "second", depends_on=(first,))
        scheduler = Scheduler(state, registry)

        def execute(step):
            calls.append(str(step["step_key"]))
            return {"step": step["step_key"]}

        assert scheduler.run(goal, execute) is GoalStatus.COMPLETE
        assert calls == ["first", "second"]

    with StateDatabase(path) as restarted:
        assert restarted.goal_step_counts(goal) == {StepStatus.COMPLETE.value: 2}


def test_scheduler_retries_failure_then_completes(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilityMetadata(name="flaky", max_attempts=2))
    with StateDatabase(tmp_path / "state.db") as state:
        goal = state.create_goal("test.retry")
        state.create_step(goal, "flaky", "flaky")
        attempts = 0

        def execute(_step):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("temporary")
            return "ok"

        assert Scheduler(state, registry).run(goal, execute) is GoalStatus.COMPLETE
        assert attempts == 2


def test_scheduler_marks_goal_failed_after_retry_budget(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    registry.register(CapabilityMetadata(name="broken", max_attempts=1))
    with StateDatabase(tmp_path / "state.db") as state:
        goal = state.create_goal("test.failure")
        state.create_step(goal, "broken", "broken")

        def execute(_step):
            raise RuntimeError("permanent")

        assert Scheduler(state, registry).run(goal, execute) is GoalStatus.FAILED
        assert state.goal_step_counts(goal) == {StepStatus.FAILED.value: 1}


def test_scheduler_honors_timeout_and_cancellation(tmp_path: Path) -> None:
    registry = CapabilityRegistry()
    registry.register(
        CapabilityMetadata(name="slow", max_attempts=1, timeout_seconds=0.01)
    )
    registry.register(CapabilityMetadata(name="cancelled"))
    with StateDatabase(tmp_path / "state.db") as state:
        timed_out = state.create_goal("test.timeout")
        state.create_step(timed_out, "slow", "slow")

        def slow(_step):
            sleep(0.05)
            return "late"

        assert Scheduler(state, registry).run(timed_out, slow) is GoalStatus.FAILED

        cancelled = state.create_goal("test.cancel")
        state.create_step(cancelled, "cancelled", "cancelled")
        event = Event()
        event.set()
        assert (
            Scheduler(state, registry).run(
                cancelled, lambda _: ExecutionResult("never"), cancel_event=event
            )
            is GoalStatus.CANCELLED
        )
