from __future__ import annotations

import json
from pathlib import Path

from quark.state.database import SCHEMA_VERSION, StateDatabase, payload_digest
from quark.state.models import StepStatus


def test_database_initializes_schema_and_tables(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "quark.db"
    with StateDatabase(database_path) as state:
        version = state.connection.execute("PRAGMA user_version").fetchone()[0]
        tables = {
            row[0]
            for row in state.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }

    assert version == SCHEMA_VERSION
    assert {
        "goals",
        "tasks",
        "steps",
        "step_dependencies",
        "step_results",
        "facts",
        "capabilities",
        "events",
        "errors",
        "permissions",
        "model_calls",
    } <= tables


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    with StateDatabase(tmp_path / "state.db") as state:
        try:
            with state.transaction() as connection:
                connection.execute(
                    "INSERT INTO goals(goal_type,status,created_at,updated_at) VALUES(?,?,?,?)",
                    ("test", "running", "now", "now"),
                )
                raise RuntimeError("abort")
        except RuntimeError:
            pass
        assert state.connection.execute("SELECT COUNT(*) FROM goals").fetchone()[0] == 0


def test_dag_steps_resume_without_rerunning_completed_step(tmp_path: Path) -> None:
    path = tmp_path / "state.db"
    with StateDatabase(path) as state:
        goal_id = state.create_goal("test.workflow", "fixture.md")
        first = state.create_step(
            goal_id, "read", "filesystem.read_file", input_value={"path": "fixture.md"}
        )
        second = state.create_step(
            goal_id, "write", "filesystem.write_file", depends_on=(first,)
        )
        claimed = state.claim_ready_step(goal_id)
        assert claimed is not None and claimed["id"] == first
        assert claimed["attempt"] == 1
        state.complete_step(
            first, {"content_digest": "abc"}, validation={"ok": True}, duration_ms=2.5
        )

    with StateDatabase(path) as restarted:
        claimed = restarted.claim_ready_step(goal_id)
        assert claimed is not None and claimed["id"] == second
        assert claimed["attempt"] == 1
        assert restarted.claim_ready_step(goal_id) is None
        snapshot = restarted.inspect_goal(goal_id)
        assert snapshot is not None
        assert snapshot["steps"][0]["status"] == StepStatus.COMPLETE.value
        assert snapshot["steps"][1]["status"] == StepStatus.RUNNING.value


def test_audit_records_errors_and_model_calls_without_prompt_contents(
    tmp_path: Path,
) -> None:
    with StateDatabase(tmp_path / "state.db") as state:
        goal_id = state.create_goal("test.audit")
        step_id = state.create_step(goal_id, "semantic", "text.classify")
        state.record_event(
            "note_read",
            goal_id=goal_id,
            step_id=step_id,
            payload={"path": "Inbox/A.md"},
        )
        state.record_event(
            "patch_proposed",
            goal_id=goal_id,
            step_id=step_id,
            payload={"path": "Inbox/A.md"},
        )
        state.record_model_call(
            provider="ollama",
            model="tiny",
            prompt="private prompt content",
            output={"choice": "research"},
            goal_id=goal_id,
            step_id=step_id,
            input_tokens=10,
            output_tokens=2,
            duration_ms=12,
            attempt=1,
        )
        state.record_error(
            "ValidationError",
            "invalid enum",
            goal_id=goal_id,
            step_id=step_id,
            attempt=1,
        )
        snapshot = state.inspect_goal(goal_id)

    assert snapshot is not None
    assert len(snapshot["events"]) == 5
    assert len(snapshot["errors"]) == 1
    assert len(snapshot["model_calls"]) == 1
    assert "private prompt content" not in json.dumps(snapshot)
    assert payload_digest({"b": 2, "a": 1}) == payload_digest({"a": 1, "b": 2})


def test_explain_change_finds_audit_events_by_note_path(tmp_path: Path) -> None:
    with StateDatabase(tmp_path / "state.db") as state:
        goal_id = state.create_goal("obsidian.organize_note", "Inbox/A.md")
        state.record_event(
            "write_applied",
            goal_id=goal_id,
            payload={"path": "Inbox/A.md", "hash": "abc"},
        )
        state.record_event("other", goal_id=goal_id, payload={"path": "Inbox/B.md"})
        events = state.explain_change("Inbox/A.md")

    assert [event["event_type"] for event in events] == [
        "goal_created",
        "write_applied",
    ]
