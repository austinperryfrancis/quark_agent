"""SQLite persistence for resumable workflows and audit records."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

from quark.state.models import GoalStatus, StepStatus, enum_value

SCHEMA_VERSION = 1

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS goals (
    id INTEGER PRIMARY KEY,
    goal_type TEXT NOT NULL,
    target TEXT,
    status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    task_key TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(goal_id, task_key)
);
CREATE TABLE IF NOT EXISTS steps (
    id INTEGER PRIMARY KEY,
    task_id INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
    goal_id INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt INTEGER NOT NULL DEFAULT 0,
    input_digest TEXT,
    input_ref TEXT,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(goal_id, step_key)
);
CREATE TABLE IF NOT EXISTS step_dependencies (
    step_id INTEGER NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    depends_on_step_id INTEGER NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    PRIMARY KEY(step_id, depends_on_step_id)
);
CREATE TABLE IF NOT EXISTS step_results (
    id INTEGER PRIMARY KEY,
    step_id INTEGER NOT NULL REFERENCES steps(id) ON DELETE CASCADE,
    result_json TEXT,
    result_digest TEXT,
    validation_ok INTEGER NOT NULL,
    validation_json TEXT,
    duration_ms REAL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
    id INTEGER PRIMARY KEY,
    fact_key TEXT NOT NULL UNIQUE,
    value_json TEXT NOT NULL,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS capabilities (
    name TEXT PRIMARY KEY,
    metadata_json TEXT NOT NULL,
    loaded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    step_id INTEGER REFERENCES steps(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS errors (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    step_id INTEGER REFERENCES steps(id) ON DELETE SET NULL,
    error_type TEXT NOT NULL,
    message TEXT NOT NULL,
    traceback TEXT,
    attempt INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS permissions (
    id INTEGER PRIMARY KEY,
    capability TEXT NOT NULL,
    risk_class TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY,
    goal_id INTEGER REFERENCES goals(id) ON DELETE SET NULL,
    step_id INTEGER REFERENCES steps(id) ON DELETE SET NULL,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    prompt_digest TEXT NOT NULL,
    output_digest TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    duration_ms REAL,
    attempt INTEGER,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_steps_ready ON steps(goal_id, status);
CREATE INDEX IF NOT EXISTS idx_events_goal ON events(goal_id, created_at);
CREATE INDEX IF NOT EXISTS idx_errors_goal ON errors(goal_id, created_at);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def payload_digest(value: Any) -> str:
    """Create a stable digest for large inputs without storing their contents."""
    return sha256(_json(value).encode("utf-8")).hexdigest()


class StateDatabase:
    """Small SQLite state store with transaction and resume helpers."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self.connection = self._connect()
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        return connection

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> StateDatabase:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a transaction and roll it back on any exception."""
        with self._lock:
            try:
                self.connection.execute("BEGIN IMMEDIATE")
                yield self.connection
            except Exception:
                self.connection.rollback()
                raise
            else:
                self.connection.commit()

    def initialize(self) -> None:
        with self._lock:
            version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise RuntimeError(
                    f"database schema {version} is newer than supported {SCHEMA_VERSION}"
                )
            if version < 1:
                self.connection.executescript(_MIGRATION_1)
                self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                self.connection.commit()

    def create_goal(
        self,
        goal_type: str,
        target: str | None = None,
        metadata: Any | None = None,
        status: GoalStatus = GoalStatus.RUNNING,
    ) -> int:
        now = _now()
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO goals(goal_type,target,status,metadata_json,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?)",
                (
                    goal_type,
                    target,
                    enum_value(status),
                    _json(metadata or {}),
                    now,
                    now,
                ),
            )
            goal_id = cast(int, cursor.lastrowid)
        self.record_event("goal_created", goal_id=goal_id, payload={"target": target})
        return goal_id

    def set_goal_status(self, goal_id: int, status: GoalStatus) -> None:
        with self.transaction() as connection:
            connection.execute(
                "UPDATE goals SET status=?, updated_at=? WHERE id=?",
                (enum_value(status), _now(), goal_id),
            )

    def create_task(
        self, goal_id: int, task_key: str, status: StepStatus = StepStatus.PENDING
    ) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO tasks(goal_id,task_key,status,created_at) VALUES(?,?,?,?)",
                (goal_id, task_key, enum_value(status), _now()),
            )
            return cast(int, cursor.lastrowid)

    def create_step(
        self,
        goal_id: int,
        step_key: str,
        operation: str,
        *,
        task_id: int | None = None,
        input_value: Any | None = None,
        input_ref: str | None = None,
        depends_on: tuple[int, ...] = (),
    ) -> int:
        digest = payload_digest(input_value) if input_value is not None else None
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO steps(goal_id,task_id,step_key,operation,status,input_digest,input_ref) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    goal_id,
                    task_id,
                    step_key,
                    operation,
                    StepStatus.PENDING.value,
                    digest,
                    input_ref,
                ),
            )
            step_id = cast(int, cursor.lastrowid)
            connection.executemany(
                "INSERT INTO step_dependencies(step_id,depends_on_step_id) VALUES(?,?)",
                ((step_id, dependency) for dependency in depends_on),
            )
            return step_id

    def refresh_ready_steps(self, goal_id: int) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                """UPDATE steps SET status=? WHERE goal_id=? AND status=? AND NOT EXISTS (
                    SELECT 1 FROM step_dependencies d JOIN steps dependency
                    ON dependency.id=d.depends_on_step_id
                    WHERE d.step_id=steps.id AND dependency.status != ?
                )""",
                (
                    StepStatus.READY.value,
                    goal_id,
                    StepStatus.PENDING.value,
                    StepStatus.COMPLETE.value,
                ),
            )
            return cursor.rowcount

    def claim_ready_step(self, goal_id: int) -> sqlite3.Row | None:
        with self.transaction() as connection:
            connection.execute(
                """UPDATE steps SET status=? WHERE goal_id=? AND status=? AND NOT EXISTS (
                    SELECT 1 FROM step_dependencies d JOIN steps dependency
                    ON dependency.id=d.depends_on_step_id
                    WHERE d.step_id=steps.id AND dependency.status != ?
                )""",
                (
                    StepStatus.READY.value,
                    goal_id,
                    StepStatus.PENDING.value,
                    StepStatus.COMPLETE.value,
                ),
            )
            row = connection.execute(
                "SELECT * FROM steps WHERE goal_id=? AND status=? ORDER BY id LIMIT 1",
                (goal_id, StepStatus.READY.value),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                "UPDATE steps SET status=?, attempt=attempt+1, started_at=? WHERE id=?",
                (StepStatus.RUNNING.value, _now(), row["id"]),
            )
            return cast(
                sqlite3.Row | None,
                connection.execute(
                    "SELECT * FROM steps WHERE id=?", (row["id"],)
                ).fetchone(),
            )

    def complete_step(
        self,
        step_id: int,
        result: Any,
        *,
        validation_ok: bool = True,
        validation: Any | None = None,
        duration_ms: float | None = None,
    ) -> None:
        encoded = _json(result) if result is not None else None
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO step_results(step_id,result_json,result_digest,validation_ok,validation_json,duration_ms,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (
                    step_id,
                    encoded,
                    payload_digest(result) if result is not None else None,
                    int(validation_ok),
                    _json(validation or {}),
                    duration_ms,
                    _now(),
                ),
            )
            status = (
                StepStatus.COMPLETE.value if validation_ok else StepStatus.FAILED.value
            )
            connection.execute(
                "UPDATE steps SET status=?, completed_at=? WHERE id=?",
                (status, _now(), step_id),
            )
        self.record_event(
            "step_completed" if validation_ok else "step_validation_failed",
            step_id=step_id,
            payload={"validation_ok": validation_ok, "duration_ms": duration_ms},
        )

    def record_event(
        self,
        event_type: str,
        *,
        goal_id: int | None = None,
        step_id: int | None = None,
        payload: Any | None = None,
    ) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO events(goal_id,step_id,event_type,payload_json,created_at) VALUES(?,?,?,?,?)",
                (goal_id, step_id, event_type, _json(payload or {}), _now()),
            )
            return cast(int, cursor.lastrowid)

    def record_error(
        self,
        error_type: str,
        message: str,
        *,
        goal_id: int | None = None,
        step_id: int | None = None,
        traceback: str | None = None,
        attempt: int | None = None,
    ) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO errors(goal_id,step_id,error_type,message,traceback,attempt,created_at) VALUES(?,?,?,?,?,?,?)",
                (goal_id, step_id, error_type, message, traceback, attempt, _now()),
            )
            error_id = cast(int, cursor.lastrowid)
        self.record_event(
            "error_recorded",
            goal_id=goal_id,
            step_id=step_id,
            payload={"error_id": error_id, "error_type": error_type},
        )
        return error_id

    def upsert_capability(self, name: str, metadata: Any) -> None:
        """Persist the latest machine-readable capability registration."""
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO capabilities(name,metadata_json,loaded_at) VALUES(?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET metadata_json=excluded.metadata_json, loaded_at=excluded.loaded_at",
                (name, _json(metadata), _now()),
            )

    def record_permission(
        self,
        capability: str,
        risk_class: str,
        decision: str,
        reason: str | None = None,
    ) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO permissions(capability,risk_class,decision,reason,created_at) VALUES(?,?,?,?,?)",
                (capability, risk_class, decision, reason, _now()),
            )
            return cast(int, cursor.lastrowid)

    def upsert_fact(self, fact_key: str, value: Any, source: str | None = None) -> None:
        """Persist a small named fact outside conversation history."""
        now = _now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO facts(fact_key,value_json,source,created_at,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(fact_key) DO UPDATE SET value_json=excluded.value_json, source=excluded.source, updated_at=excluded.updated_at",
                (fact_key, _json(value), source, now, now),
            )

    def record_model_call(
        self,
        *,
        provider: str,
        model: str,
        prompt: Any,
        output: Any | None = None,
        goal_id: int | None = None,
        step_id: int | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        duration_ms: float | None = None,
        attempt: int | None = None,
    ) -> int:
        with self.transaction() as connection:
            cursor = connection.execute(
                "INSERT INTO model_calls(goal_id,step_id,provider,model,prompt_digest,output_digest,input_tokens,output_tokens,duration_ms,attempt,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    goal_id,
                    step_id,
                    provider,
                    model,
                    payload_digest(prompt),
                    payload_digest(output) if output is not None else None,
                    input_tokens,
                    output_tokens,
                    duration_ms,
                    attempt,
                    _now(),
                ),
            )
            call_id = cast(int, cursor.lastrowid)
        self.record_event(
            "model_call_recorded",
            goal_id=goal_id,
            step_id=step_id,
            payload={"model_call_id": call_id},
        )
        return call_id

    def inspect_goal(self, goal_id: int) -> dict[str, Any] | None:
        goal = self.connection.execute(
            "SELECT * FROM goals WHERE id=?", (goal_id,)
        ).fetchone()
        if goal is None:
            return None
        steps = self.connection.execute(
            "SELECT * FROM steps WHERE goal_id=? ORDER BY id", (goal_id,)
        ).fetchall()
        events = self.connection.execute(
            "SELECT * FROM events WHERE goal_id=? ORDER BY id", (goal_id,)
        ).fetchall()
        errors = self.connection.execute(
            "SELECT * FROM errors WHERE goal_id=? ORDER BY id", (goal_id,)
        ).fetchall()
        model_calls = self.connection.execute(
            "SELECT * FROM model_calls WHERE goal_id=? ORDER BY id", (goal_id,)
        ).fetchall()
        return {
            "goal": dict(goal),
            "steps": [dict(step) for step in steps],
            "events": [dict(event) for event in events],
            "errors": [dict(error) for error in errors],
            "model_calls": [dict(call) for call in model_calls],
        }

    def explain_change(self, note_path: str) -> list[dict[str, Any]]:
        rows = self.connection.execute(
            "SELECT * FROM events WHERE payload_json LIKE ? ORDER BY id",
            (f"%{note_path}%",),
        ).fetchall()
        return [dict(row) for row in rows]
