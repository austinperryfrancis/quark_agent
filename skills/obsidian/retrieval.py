"""Deterministic local indexing and related-note candidate retrieval."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from quark.state.database import StateDatabase
from skills.obsidian.operations.list_notes import DEFAULT_EXCLUSIONS, list_notes
from skills.obsidian.operations.parse_frontmatter import parse_note
from skills.obsidian.operations.read_note import read_note


def _json(value: Any) -> str:
    return json.dumps(sorted(set(str(item) for item in value)), separators=(",", ":"))


class NoteIndex:
    """SQLite-backed note metadata/FTS index with hash-based incremental sync."""

    def __init__(self, state: StateDatabase) -> None:
        self.state = state

    def sync(
        self, vault_path: Path, exclusions: Iterable[str] = DEFAULT_EXCLUSIONS
    ) -> int:
        references = list_notes(vault_path, exclusions)
        current = {reference.note_id for reference in references}
        changed = 0
        with self.state.transaction() as connection:
            indexed = {
                str(row["note_id"]): str(row["content_hash"])
                for row in connection.execute(
                    "SELECT note_id, content_hash FROM note_index"
                )
            }
            for note_id in set(indexed) - current:
                connection.execute("DELETE FROM note_index WHERE note_id=?", (note_id,))
                connection.execute("DELETE FROM note_fts WHERE note_id=?", (note_id,))
            for reference in references:
                document = read_note(vault_path, reference)
                if indexed.get(note_id := document.note_id) == document.content_hash:
                    continue
                parsed = parse_note(document, vault_path, exclusions)
                data = parsed.frontmatter.data
                title = str(data.get("title") or document.path.stem)
                project = data.get("project")
                people = data.get("people", [])
                if isinstance(people, str):
                    people = [people]
                tags = [*parsed.yaml_tags, *parsed.inline_tags]
                outgoing = [
                    link.resolved_note_id
                    for link in parsed.links
                    if link.resolved_note_id
                ]
                connection.execute("DELETE FROM note_fts WHERE note_id=?", (note_id,))
                connection.execute(
                    "INSERT INTO note_fts(note_id,title,body) VALUES(?,?,?)",
                    (note_id, title, document.analysis_content),
                )
                connection.execute(
                    "INSERT INTO note_index(note_id,content_hash,title,body,project,tags_json,people_json,outgoing_json,backlinks_json,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,datetime('now')) "
                    "ON CONFLICT(note_id) DO UPDATE SET content_hash=excluded.content_hash,title=excluded.title,body=excluded.body,project=excluded.project,tags_json=excluded.tags_json,people_json=excluded.people_json,outgoing_json=excluded.outgoing_json,updated_at=excluded.updated_at",
                    (
                        note_id,
                        document.content_hash,
                        title,
                        document.analysis_content,
                        project,
                        _json(tags),
                        _json(people),
                        _json(outgoing),
                        "[]",
                    ),
                )
                changed += 1
            connection.execute("UPDATE note_index SET backlinks_json='[]'")
            rows = connection.execute(
                "SELECT note_id, outgoing_json FROM note_index"
            ).fetchall()
            backlinks: dict[str, list[str]] = {}
            for row in rows:
                for target in json.loads(row["outgoing_json"]):
                    backlinks.setdefault(target, []).append(str(row["note_id"]))
            for note_id, sources in backlinks.items():
                connection.execute(
                    "UPDATE note_index SET backlinks_json=? WHERE note_id=?",
                    (_json(sources), note_id),
                )
        return changed

    def get(self, note_id: str) -> dict[str, Any] | None:
        row = self.state.connection.execute(
            "SELECT * FROM note_index WHERE note_id=?", (note_id,)
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ("tags_json", "people_json", "outgoing_json", "backlinks_json"):
            result[field[:-5]] = json.loads(result.pop(field))
        return result

    def candidates(self, note_id: str, *, limit: int = 8) -> list[dict[str, Any]]:
        target = self.get(note_id)
        if target is None:
            return []
        query = re.sub(r"[^\w ]+", " ", f"{target['title']} {target['body']}").strip()
        if not query:
            return []
        rows = self.state.connection.execute(
            "SELECT n.*, bm25(note_fts) AS rank FROM note_fts JOIN note_index n USING(note_id) WHERE note_fts MATCH ? AND n.note_id != ? ORDER BY rank LIMIT ?",
            (" OR ".join(query.split()[:24]), note_id, max(limit * 3, limit)),
        ).fetchall()
        target_tags = set(target["tags"])
        result: list[dict[str, Any]] = []
        for row in rows:
            item = self.get(str(row["note_id"]))
            if item is None:
                continue
            item["score"] = len(target_tags.intersection(item["tags"])) + (
                1 if target["project"] and target["project"] == item["project"] else 0
            )
            result.append(item)
        return sorted(
            result,
            key=lambda item: (-int(item["score"]), str(item["note_id"]).casefold()),
        )[:limit]
