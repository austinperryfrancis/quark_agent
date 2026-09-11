"""Small, inspectable SQLite storage for Quark's Pydantic domain models."""

from __future__ import annotations

import json
from pathlib import Path
import sqlite3

from quark.models import (
    ExecutionEvent,
    PendingInteraction,
    Run,
    Session,
    SkillCall,
    SkillCallRevision,
)

_SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    session_key TEXT NOT NULL UNIQUE,
    data_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    status TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS runs_session_id_idx ON runs(session_id);

CREATE TABLE IF NOT EXISTS skill_calls (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    parent_call_id TEXT,
    skill_name TEXT NOT NULL,
    status TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS skill_calls_run_id_idx ON skill_calls(run_id);
CREATE INDEX IF NOT EXISTS skill_calls_parent_idx ON skill_calls(parent_call_id);

CREATE TABLE IF NOT EXISTS skill_call_revisions (
    skill_call_id TEXT NOT NULL,
    revision INTEGER NOT NULL,
    data_json TEXT NOT NULL,
    PRIMARY KEY (skill_call_id, revision),
    FOREIGN KEY (skill_call_id) REFERENCES skill_calls(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS pending_interactions (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    skill_call_id TEXT,
    interaction_type TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS pending_run_id_idx ON pending_interactions(run_id);

CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    run_id TEXT NOT NULL,
    skill_call_id TEXT,
    created_at TEXT NOT NULL,
    data_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS events_run_id_idx ON events(run_id, sequence);
"""


class SQLiteStateStore:
    """Persist complete runtime objects without an ORM or hidden unit of work."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(_SCHEMA)
        row = self.connection.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self.connection.execute("INSERT INTO schema_version(version) VALUES (1)")
        elif row["version"] != 1:
            raise RuntimeError(f"Unsupported database schema version {row['version']}")
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def save_session(self, session: Session) -> None:
        with self.connection:
            self._save_session(session)

    def load_session(self, session_id: str) -> Session | None:
        row = self.connection.execute(
            "SELECT data_json FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return Session.model_validate_json(row["data_json"]) if row else None

    def load_session_by_key(self, session_key: str) -> Session | None:
        row = self.connection.execute(
            "SELECT data_json FROM sessions WHERE session_key = ?", (session_key,)
        ).fetchone()
        return Session.model_validate_json(row["data_json"]) if row else None

    def list_sessions(self) -> tuple[Session, ...]:
        rows = self.connection.execute(
            "SELECT data_json FROM sessions ORDER BY rowid"
        ).fetchall()
        return tuple(Session.model_validate_json(row["data_json"]) for row in rows)

    def save_run(self, run: Run) -> None:
        with self.connection:
            self._save_run(run)

    def load_run(self, run_id: str) -> Run | None:
        row = self.connection.execute(
            "SELECT data_json FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        return Run.model_validate_json(row["data_json"]) if row else None

    def list_runs(self) -> tuple[Run, ...]:
        rows = self.connection.execute(
            "SELECT data_json FROM runs ORDER BY rowid"
        ).fetchall()
        return tuple(Run.model_validate_json(row["data_json"]) for row in rows)

    def save_skill_call(self, call: SkillCall) -> None:
        with self.connection:
            self._save_skill_call(call)

    def load_skill_call(self, call_id: str) -> SkillCall | None:
        row = self.connection.execute(
            "SELECT data_json FROM skill_calls WHERE id = ?", (call_id,)
        ).fetchone()
        if row is None:
            return None
        data = json.loads(row["data_json"])
        revision_rows = self.connection.execute(
            """
            SELECT data_json FROM skill_call_revisions
            WHERE skill_call_id = ? ORDER BY revision
            """,
            (call_id,),
        ).fetchall()
        data["revisions"] = [json.loads(item["data_json"]) for item in revision_rows]
        return SkillCall.model_validate(data)

    def list_run_calls(self, run_id: str) -> tuple[SkillCall, ...]:
        rows = self.connection.execute(
            "SELECT id FROM skill_calls WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
        return tuple(self.load_skill_call(row["id"]) for row in rows)

    def save_pending_interaction(self, pending: PendingInteraction) -> None:
        with self.connection:
            self._save_pending_interaction(pending)

    def load_pending_interaction(
        self, interaction_id: str
    ) -> PendingInteraction | None:
        row = self.connection.execute(
            "SELECT data_json FROM pending_interactions WHERE id = ?",
            (interaction_id,),
        ).fetchone()
        return PendingInteraction.model_validate_json(row["data_json"]) if row else None

    def delete_pending_interaction(self, interaction_id: str) -> None:
        with self.connection:
            self.connection.execute(
                "DELETE FROM pending_interactions WHERE id = ?", (interaction_id,)
            )

    def append_event(self, event: ExecutionEvent) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO events(
                    id, event_type, run_id, skill_call_id, created_at, data_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.type.value,
                    event.run_id,
                    event.skill_call_id,
                    event.created_at.isoformat(),
                    event.model_dump_json(),
                ),
            )

    def list_events(self, run_id: str) -> tuple[ExecutionEvent, ...]:
        rows = self.connection.execute(
            "SELECT data_json FROM events WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        ).fetchall()
        return tuple(ExecutionEvent.model_validate_json(row["data_json"]) for row in rows)

    def save_runtime_state(
        self,
        *,
        session: Session,
        run: Run,
        calls: tuple[SkillCall, ...],
        pending: PendingInteraction | None,
    ) -> None:
        """Atomically persist one Run and its current conversational state."""
        with self.connection:
            self._save_session(session)
            self._save_run(run)
            for call in calls:
                self._save_skill_call(call)
            if pending is not None:
                self._save_pending_interaction(pending)
            else:
                self.connection.execute(
                    "DELETE FROM pending_interactions WHERE run_id = ?", (run.id,)
                )

    def _save_session(self, session: Session) -> None:
        self.connection.execute(
            """
            INSERT INTO sessions(id, session_key, data_json) VALUES (?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_key = excluded.session_key,
                data_json = excluded.data_json
            """,
            (session.id, session.session_key, session.model_dump_json()),
        )

    def _save_run(self, run: Run) -> None:
        self.connection.execute(
            """
            INSERT INTO runs(id, session_id, status, data_json) VALUES (?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                session_id = excluded.session_id,
                status = excluded.status,
                data_json = excluded.data_json
            """,
            (run.id, run.session_id, run.status.value, run.model_dump_json()),
        )

    def _save_skill_call(self, call: SkillCall) -> None:
        data = call.model_dump(mode="json")
        data["revisions"] = []
        self.connection.execute(
            """
            INSERT INTO skill_calls(
                id, run_id, parent_call_id, skill_name, status, data_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                run_id = excluded.run_id,
                parent_call_id = excluded.parent_call_id,
                skill_name = excluded.skill_name,
                status = excluded.status,
                data_json = excluded.data_json
            """,
            (
                call.id,
                call.run_id,
                call.parent_call_id,
                call.skill_name,
                call.status.value,
                json.dumps(data),
            ),
        )
        for revision in call.revisions:
            self._save_revision(call.id, revision)

    def _save_revision(self, call_id: str, revision: SkillCallRevision) -> None:
        self.connection.execute(
            """
            INSERT INTO skill_call_revisions(skill_call_id, revision, data_json)
            VALUES (?, ?, ?)
            ON CONFLICT(skill_call_id, revision) DO NOTHING
            """,
            (call_id, revision.revision, revision.model_dump_json()),
        )

    def _save_pending_interaction(self, pending: PendingInteraction) -> None:
        self.connection.execute(
            """
            INSERT INTO pending_interactions(
                id, run_id, skill_call_id, interaction_type, data_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                run_id = excluded.run_id,
                skill_call_id = excluded.skill_call_id,
                interaction_type = excluded.interaction_type,
                data_json = excluded.data_json
            """,
            (
                pending.id,
                pending.run_id,
                pending.skill_call_id,
                pending.type.value,
                pending.model_dump_json(),
            ),
        )
