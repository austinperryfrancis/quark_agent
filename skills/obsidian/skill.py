"""Public executable adapter for the Obsidian skill."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

from quark.agent.lifecycle import (
    Proposal,
    Validation,
    ValidationMode,
    payload_digest,
    proposal_payload,
)
from quark.agent.models import IntentDefinition
from quark.agent.skills import SkillContext, SkillResult
from quark.models.provider import ModelProviderError
from quark.models.semantic import SemanticError, SemanticRuntime
from skills.obsidian.operations.apply_changes import apply_changes
from skills.obsidian.operations.generate_frontmatter import (
    FrontmatterConfig,
    generate_frontmatter,
)
from skills.obsidian.operations.list_notes import list_notes
from skills.obsidian.operations.parse_frontmatter import parse_note
from skills.obsidian.operations.read_note import read_note
from skills.obsidian.retrieval import NoteIndex
from skills.obsidian.semantic import (
    choose_folder,
    classify_project,
    generate_tags,
    judge_relationship,
    verify_relationship,
)
from skills.obsidian.taxonomy import TagPolicy, TagTaxonomy


class ObsidianSkill:
    name = "obsidian"

    def __init__(self, context: SkillContext) -> None:
        if context.vault is None:
            raise ValueError("Obsidian skill requires a configured vault")
        self.vault = context.vault
        self.state = context.state
        self.config = context.config
        self.index = NoteIndex(context.state)
        self.provider = context.provider
        self.taxonomy = TagTaxonomy(context.state)
        self.runtime = (
            SemanticRuntime(
                context.provider,
                state=context.state,
                retries=context.config.retries,
                prompt_root=Path("skills/obsidian/prompts"),
            )
            if context.provider is not None
            else None
        )

    def intents(self) -> tuple[IntentDefinition, ...]:
        return (
            IntentDefinition(
                "obsidian.search",
                self.name,
                "Search or answer questions about vault notes",
                keywords=("notes", "note", "vault", "find", "search"),
            ),
            IntentDefinition(
                "obsidian.organize",
                self.name,
                "Organize a note or Obsidian inbox",
                process=True,
                write=True,
                keywords=("organize", "clean", "classify", "inbox", "tag"),
            ),
            IntentDefinition(
                "obsidian.move_note",
                self.name,
                "Move inbox notes into their appropriate vault folders",
                process=True,
                write=True,
                keywords=("move", "file", "folder", "inbox"),
            ),
            IntentDefinition(
                "obsidian.add_wikilinks",
                self.name,
                "Find and add links to related vault notes",
                process=True,
                write=True,
                keywords=("link", "links", "related", "connect"),
            ),
            IntentDefinition(
                "obsidian.organize_and_file",
                self.name,
                "Classify, tag, link, and file inbox notes as one workflow",
                process=True,
                write=True,
                keywords=("organize", "file", "ingest", "inbox"),
            ),
        )

    def propose(self, operation: str, arguments: dict[str, Any]) -> Proposal:
        """Lifecycle adapter: every Obsidian write starts as a typed proposal."""
        result = self.execute(operation, arguments)
        payload = {**arguments, **dict(result.proposed_action or result.data)}
        read_only = operation == "obsidian.search"
        deterministic = Validation(
            read_only or result.proposed_action is not None,
            "proposal payload is present"
            if read_only or result.proposed_action is not None
            else "no writable proposal",
        )
        semantic = Validation(
            self.runtime is not None,
            "semantic runtime available"
            if self.runtime is not None
            else "semantic runtime unavailable",
        )
        mode = (
            ValidationMode.OFF
            if operation == "obsidian.search"
            else ValidationMode(self.config.workflows.validation)
        )
        return Proposal(
            skill=self.name,
            operation=operation,
            payload=payload,
            deterministic=deterministic,
            semantic=semantic,
            mode=mode,
            text=result.text,
            request=str(arguments.get("message", "")),
            input_digest=payload_digest(arguments),
        )

    def revise_proposal(self, proposal: Proposal, feedback: str) -> Proposal:
        result = self._revise_implementation(proposal.operation, proposal.payload, feedback)
        return Proposal(
            skill=proposal.skill,
            operation=proposal.operation,
            payload=dict(result.proposed_action or proposal.payload),
            deterministic=proposal.deterministic,
            semantic=proposal.semantic,
            mode=proposal.mode,
            revision=proposal.revision + 1,
            text=result.text,
            request=proposal.request,
            input_digest=proposal.input_digest,
        )

    def explain_proposal(self, proposal: Proposal, question: str) -> str:
        return (
            proposal.text
            + "\n\nValidation: "
            + proposal.deterministic.reason
            + "; "
            + proposal.semantic.reason
        )

    def apply_proposal(self, proposal: Proposal) -> dict[str, Any]:
        if proposal.operation == "obsidian.search":
            result = self.execute(proposal.operation, proposal.payload)
            return {"text": result.text, "data": result.data, "next": None}
        result = self.apply(proposal.operation, proposal.payload)
        next_payload = result.proposed_action
        if next_payload is not None:
            next_payload = proposal_payload(
                Proposal(
                    skill=self.name,
                    operation=proposal.operation,
                    payload=next_payload,
                    deterministic=Validation(True, "stage payload is present"),
                    semantic=Validation(
                        self.runtime is not None, "semantic runtime checked"
                    ),
                    mode=proposal.mode,
                    revision=proposal.revision + 1,
                    text=result.text,
                    request=proposal.request,
                    input_digest=payload_digest(next_payload),
                )
            )
        return {
            "text": result.text,
            "data": result.data,
            "next": next_payload,
        }

    def execute(self, intent: str, arguments: dict[str, Any]) -> SkillResult:
        if intent == "obsidian.organize_and_file":
            result = self.execute("obsidian.organize", arguments)
            all_updates = dict((result.proposed_action or {}).get("updates", {}))
            frontmatter = {
                note_id: {key: value for key, value in update.items() if key != "tags"}
                for note_id, update in all_updates.items()
            }
            tags = {
                note_id: {"tags": update.get("tags", [])}
                for note_id, update in all_updates.items()
            }
            payload = {
                "stage": "frontmatter",
                "note_ids": list(all_updates),
                "updates": frontmatter,
                "tag_updates": tags,
            }
            lines = [
                f"- {note_id}: {update}" for note_id, update in frontmatter.items()
            ]
            return SkillResult(
                "Proposed frontmatter:\n"
                + "\n".join(lines)
                + "\nApply frontmatter? [y/N]",
                payload,
                payload,
            )
        if intent == "obsidian.search":
            self.index.sync(self.vault, self.config.vault.exclude)
            message = str(arguments.get("message", ""))
            stopwords = {
                "about",
                "find",
                "from",
                "have",
                "notes",
                "note",
                "search",
                "show",
                "that",
                "the",
                "vault",
                "what",
                "with",
            }
            words = [
                word.strip("?,.")
                for word in message.split()
                if len(word.strip("?,.")) > 2
                and word.strip("?,.").casefold() not in stopwords
            ]
            if self.provider is not None:
                schema = {
                    "type": "object",
                    "required": ["terms"],
                    "properties": {
                        "terms": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 4,
                        }
                    },
                    "additionalProperties": False,
                }
                try:
                    call = self.provider.generate(
                        "Fill the local note-search function. Return JSON only. "
                        "Extract 1-4 specific search terms; omit filler words.\n"
                        f"REQUEST: {message}",
                        schema=schema,
                        temperature=0.0,
                        max_tokens=32,
                    ).text
                    terms = json.loads(call).get("terms", [])
                    if isinstance(terms, list) and all(
                        isinstance(term, str) for term in terms
                    ):
                        words = [term.strip() for term in terms if term.strip()]
                except (
                    json.JSONDecodeError,
                    ModelProviderError,
                    TypeError,
                    ValueError,
                ):
                    pass
            rows = self.state.connection.execute(
                "SELECT note_id,title FROM note_fts WHERE note_fts MATCH ? LIMIT 8",
                (" OR ".join(words[-8:]) or "notes",),
            ).fetchall()
            if not rows:
                return SkillResult("I couldn’t find a matching note in the vault.")
            items = "\n".join(
                f"- [[{str(row['note_id']).removesuffix('.md')}]] — {row['title']}"
                for row in rows
            )
            return SkillResult(
                f"I found these notes:\n{items}",
                {"note_ids": [row["note_id"] for row in rows]},
            )
        if intent == "obsidian.organize":
            notes = list_notes(
                self.vault / self.config.vault.inbox, self.config.vault.exclude
            )
            if not notes:
                return SkillResult("The configured inbox contains no Markdown notes.")
            note_ids: list[str] = []
            updates: dict[str, dict[str, Any]] = {}
            self.taxonomy.sync_vault(self.vault, self.config.vault.exclude)
            for note in notes:
                note_id = str((self.config.vault.inbox / note.relative_path).as_posix())
                note_ids.append(note_id)
                parsed = parse_note(
                    read_note(self.vault, note_id),
                    self.vault,
                    self.config.vault.exclude,
                )
                fields: dict[str, Any] = {
                    "updated": parsed.document.modified_at.date().isoformat()
                }
                if self.runtime is not None:
                    projects = self.config.vault.project_choices or ["none"]
                    decision = classify_project(self.runtime, parsed.document, projects)
                    if decision.project != "none":
                        fields["project"] = decision.project
                    candidates = self.taxonomy.candidates(
                        parsed.document.analysis_content, project=decision.project
                    )
                    try:
                        tag_result = generate_tags(
                            self.runtime,
                            parsed.document,
                            candidates,
                            max_tags=self.config.frontmatter.max_tags,
                            policy=TagPolicy(
                                protected=self.config.vault.protected_tags,
                                forbidden=self.config.vault.forbidden_tags,
                                max_tags=self.config.frontmatter.max_tags,
                            ),
                        )
                        fields["tags"] = tag_result.value["tags"]
                    except SemanticError:
                        # A malformed small-model tag proposal must not abort
                        # organization of every other inbox note.
                        fields["tags"] = []
                updates[note_id] = fields
            return SkillResult(
                "I can organize these inbox notes with classified projects and tags:\n"
                + "\n".join(f"- {note_id}" for note_id in note_ids)
                + "\nApply these changes? [y/N]",
                {"note_ids": note_ids, "updates": updates},
                {"note_ids": note_ids, "updates": updates},
            )
        if intent == "obsidian.move_note":
            inbox = self.vault / self.config.vault.inbox
            request = str(arguments.get("message", "")).casefold()
            notes = list_notes(inbox, self.config.vault.exclude)
            if "note" not in request and "inbox" not in request:
                notes = [
                    note
                    for note in notes
                    if any(
                        word in note.relative_path.name.casefold()
                        for word in request.split()
                    )
                ]
            moves = []
            for note in notes:
                parsed = parse_note(
                    read_note(self.vault, inbox / note.relative_path),
                    self.vault,
                    self.config.vault.exclude,
                )
                try:
                    folder, confidence, justification = self._folder_proposal(
                        parsed.document
                    )
                except SemanticError:
                    folder, confidence, justification = (
                        "Personal",
                        0.0,
                        "invalid model proposal; review manually",
                    )
                destination = Path(folder) / note.relative_path.name
                moves.append(
                    {
                        "source": (inbox / note.relative_path).as_posix(),
                        "destination": destination.as_posix(),
                        "folder": folder,
                        "confidence": confidence,
                        "justification": justification,
                    }
                )
            if not moves:
                return SkillResult("I couldn’t find matching inbox notes to move.")
            return SkillResult(
                "I propose moving:\n"
                + "\n".join(
                    f"- {item['source']} → {item['destination']} "
                    f"(confidence {item['confidence']:.2f}: {item['justification']})"
                    for item in moves
                )
                + "\nApply these moves? [y/N]",
                {"moves": moves},
                {"moves": moves},
            )
        if intent == "obsidian.add_wikilinks":
            if self.runtime is None:
                return SkillResult("The Obsidian model runtime is unavailable.")
            self.index.sync(self.vault, self.config.vault.exclude)
            target_id = str(arguments.get("message", ""))
            rows = self.state.connection.execute(
                "SELECT note_id, title, body, content_hash FROM note_index WHERE note_id != ? LIMIT 5",
                (target_id,),
            ).fetchall()
            related_candidates: list[dict[str, Any]] = [dict(row) for row in rows]
            target = parse_note(
                read_note(self.vault, target_id), self.vault, self.config.vault.exclude
            )
            selected: list[str] = []
            for candidate in related_candidates:
                judgment = verify_relationship(
                    self.runtime,
                    judge_relationship(self.runtime, target.document, candidate),
                    target.document,
                    candidate,
                )
                if judgment.related and judgment.confidence >= 0.7:
                    selected.append(judgment.note_id)
            return SkillResult(
                "I propose adding related-note links:\n"
                + "\n".join(f"- [[{item.removesuffix('.md')}]]" for item in selected)
                + "\nApply these links? [y/N]",
                {"note_id": target_id, "related": selected},
                {"note_id": target_id, "related": selected},
            )
        raise ValueError(f"unsupported Obsidian intent: {intent}")

    def _folder_proposal(self, document: Any) -> tuple[str, float, str]:
        if self.runtime is None:
            return "Personal", 0.0, "model runtime unavailable"
        return choose_folder(
            self.runtime,
            document,
            ["Projects", "Personal", "Reading", "People", "Health", "Finance"],
        )

    def _revise_implementation(
        self, intent: str, payload: dict[str, Any], feedback: str
    ) -> SkillResult:
        """Keep user feedback inside the active workflow stage."""
        if intent != "obsidian.organize_and_file":
            return SkillResult("I can only revise the active workflow proposal.")
        stage = str(payload.get("stage", ""))
        text = feedback.casefold()
        if stage == "frontmatter":
            project = (
                "personal"
                if "personal" in text
                else "sanctions-paper"
                if "sanctions" in text or "project" in text
                else None
            )
            if project is not None:
                for note_id, update in payload.get("updates", {}).items():
                    stem = Path(str(note_id)).stem.casefold()
                    if any(word in stem for word in text.split()):
                        update["project"] = project
                lines = [
                    f"- {note_id}: {update}"
                    for note_id, update in payload.get("updates", {}).items()
                ]
                return SkillResult(
                    "Revised frontmatter:\n"
                    + "\n".join(lines)
                    + "\nApply frontmatter? [y/N]",
                    payload,
                    payload,
                )
        return SkillResult(
            "I kept the current proposal. Reply `y` to approve or `cancel` to stop.",
            payload,
            payload,
        )

    def apply(self, intent: str, payload: dict[str, Any]) -> SkillResult:
        if intent == "obsidian.organize_and_file":
            stage = str(payload.get("stage"))
            if stage == "frontmatter":
                stage_result = self.apply("obsidian.organize", payload)
                next_payload = {
                    "stage": "tags",
                    "note_ids": payload["note_ids"],
                    "updates": payload["tag_updates"],
                }
                lines = [
                    f"- {note_id}: {update['tags']}"
                    for note_id, update in payload["tag_updates"].items()
                ]
                return SkillResult(
                    stage_result.text
                    + "\n\nProposed tags:\n"
                    + "\n".join(lines)
                    + "\nApply tags? [y/N]",
                    next_payload,
                    next_payload,
                )
            if stage == "tags":
                stage_result = self.apply("obsidian.organize", payload)
                links: dict[str, list[str]] = {}
                for note_id in payload["note_ids"]:
                    try:
                        preview = self.execute(
                            "obsidian.add_wikilinks", {"message": note_id}
                        )
                        links[str(note_id)] = list(preview.data.get("related", []))
                    except SemanticError:
                        links[str(note_id)] = []
                next_payload = {"stage": "wikilinks", "links": links}
                lines = [
                    f"- {note_id}: "
                    + ", ".join(f"[[{item.removesuffix('.md')}]]" for item in related)
                    for note_id, related in links.items()
                ]
                return SkillResult(
                    stage_result.text
                    + "\n\nProposed wikilinks:\n"
                    + "\n".join(lines)
                    + "\nApply wikilinks? [y/N]",
                    next_payload,
                    next_payload,
                )
            if stage == "wikilinks":
                for note_id, related in payload["links"].items():
                    self.apply(
                        "obsidian.add_wikilinks",
                        {"note_id": note_id, "related": related},
                    )
                preview = self.execute("obsidian.move_note", {"message": "inbox notes"})
                next_payload = {"stage": "move", **(preview.proposed_action or {})}
                return SkillResult(
                    "Wikilinks applied.\n\n" + preview.text, next_payload, next_payload
                )
            if stage == "move":
                result = self.apply("obsidian.move_note", payload)
                self.index.sync(self.vault, self.config.vault.exclude)
                return result
            raise ValueError(f"unknown workflow stage: {stage}")
        if intent == "obsidian.add_wikilinks":
            note_id = str(payload["note_id"])
            path = self.vault / note_id
            document = read_note(self.vault, note_id)
            from skills.obsidian.related import render_related_section

            updated = render_related_section(
                payload.get("related", []), document.content
            )
            path.write_text(
                updated + ("\n" if not updated.endswith("\n") else ""), encoding="utf-8"
            )
            return SkillResult("Added related-note links.", {"note_id": note_id})
        if intent == "obsidian.move_note":
            moved: list[str] = []
            for item in payload.get("moves", []):
                source = (self.vault / str(item["source"])).resolve()
                destination = (self.vault / str(item["destination"])).resolve()
                if (
                    self.vault.resolve() not in source.parents
                    or self.vault.resolve() not in destination.parents
                ):
                    raise ValueError("move path escapes configured vault")
                if not source.is_file() or destination.exists():
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(source), str(destination))
                moved.append(str(item["destination"]))
            return SkillResult(f"Moved {len(moved)} note(s).", {"moved": moved})
        if intent != "obsidian.organize":
            raise ValueError(f"intent is not an applicable process: {intent}")
        applied: list[str] = []
        updates = payload.get("updates", {})
        for note_id in payload.get("note_ids", []):
            parsed = parse_note(
                read_note(self.vault, str(note_id)),
                self.vault,
                self.config.vault.exclude,
            )
            patch = generate_frontmatter(
                parsed,
                dict(updates.get(str(note_id), {}))
                if isinstance(updates, dict)
                else {},
                deterministic_fields={
                    "updated": parsed.document.modified_at.date().isoformat()
                },
                config=FrontmatterConfig(
                    field_order=tuple(self.config.frontmatter.field_order),
                    preserve_unknown_fields=self.config.frontmatter.preserve_unknown_fields,
                    overwrite_nonempty=True,
                    max_tags=self.config.frontmatter.max_tags,
                ),
            )
            if patch.diff:
                apply_changes(
                    self.vault,
                    patch,
                    dry_run=False,
                    create_backup=self.config.vault.create_backups,
                    backup_retention=self.config.vault.backup_retention,
                )
                applied.append(str(note_id))
        return SkillResult(f"Organized {len(applied)} note(s).", {"applied": applied})
