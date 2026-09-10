"""Obsidian-specific semantic microtasks built on the generic runtime."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from quark.core.context import SemanticTask
from quark.models.semantic import SemanticError, SemanticResult, SemanticRuntime
from skills.obsidian.models import NoteDocument
from skills.obsidian.taxonomy import TagPolicy, normalize_tag


class ConfidenceRoute(StrEnum):
    ACCEPT = "accept"
    VERIFY = "verify"
    RETRIEVE_MORE = "retrieve_more"
    ASK_USER = "ask_user"


@dataclass(frozen=True, slots=True)
class ProjectDecision:
    project: str
    confidence: float
    route: ConfidenceRoute
    result: SemanticResult


@dataclass(frozen=True, slots=True)
class RelationshipJudgment:
    note_id: str
    related: bool
    relationship: str
    confidence: float
    evidence: str
    result: SemanticResult


def judge_relationship(
    runtime: SemanticRuntime, target: NoteDocument, candidate: dict[str, Any]
) -> RelationshipJudgment:
    """Judge one local candidate using only short target/candidate excerpts."""
    schema = {
        "type": "object",
        "required": ["related", "relationship", "confidence", "evidence"],
        "properties": {
            "related": {"type": "boolean"},
            "relationship": {"type": "string"},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "evidence": {"type": "string"},
        },
        "additionalProperties": False,
    }
    candidate_id = str(candidate["note_id"])
    result = runtime.run(
        SemanticTask(
            operation="obsidian.judge_relationship",
            role="relationship_checker",
            facts={
                "target": target.analysis_content[:1200],
                "candidate": str(candidate.get("body", ""))[:1200],
            },
            choices=(candidate_id,),
            output_schema=schema,
            content_hash=f"{target.content_hash}:{candidate_id}:{candidate.get('content_hash', '')}",
            model_policy={"max_tokens": 48, "temperature": 0.0},
        )
    )
    return RelationshipJudgment(
        candidate_id,
        bool(result.value["related"]),
        str(result.value["relationship"]),
        float(result.value["confidence"]),
        str(result.value["evidence"]),
        result,
    )


def verify_relationship(
    runtime: SemanticRuntime,
    judgment: RelationshipJudgment,
    target: NoteDocument,
    candidate: dict[str, Any],
) -> RelationshipJudgment:
    """Verify uncertain positive judgments with a separate semantic role."""
    if not judgment.related or judgment.confidence >= 0.9:
        return judgment
    result = runtime.run(
        SemanticTask(
            operation="obsidian.verify_relationship",
            role="verifier",
            facts={
                "target": target.analysis_content[:800],
                "candidate": str(candidate.get("body", ""))[:800],
                "proposed": judgment.relationship,
            },
            choices=(str(candidate["note_id"]), "reject"),
            output_schema={
                "type": "object",
                "required": ["valid"],
                "properties": {"valid": {"type": "boolean"}},
                "additionalProperties": False,
            },
            content_hash=f"verify:{target.content_hash}:{candidate['note_id']}",
            model_policy={"max_tokens": 8, "temperature": 0.0},
        )
    )
    if not bool(result.value["valid"]):
        return RelationshipJudgment(
            judgment.note_id,
            False,
            judgment.relationship,
            judgment.confidence,
            judgment.evidence,
            result,
        )
    return judgment


def verify_project_decision(
    runtime: SemanticRuntime,
    document: NoteDocument,
    decision: ProjectDecision,
) -> bool:
    """Run a second semantic role for medium-confidence classifications."""
    if decision.route is not ConfidenceRoute.VERIFY:
        return decision.route is ConfidenceRoute.ACCEPT
    result = runtime.run(
        SemanticTask(
            operation="obsidian.verify_project",
            role="verifier",
            facts={
                "note": document.analysis_content,
                "proposed_project": decision.project,
            },
            choices=(decision.project, "reject"),
            output_schema={
                "type": "object",
                "required": ["valid"],
                "properties": {"valid": {"type": "boolean"}},
                "additionalProperties": False,
            },
            content_hash=document.content_hash,
            model_policy={"max_tokens": 8, "temperature": 0.0},
        )
    )
    return bool(result.value["valid"])


def generate_tags(
    runtime: SemanticRuntime,
    document: NoteDocument,
    candidates: list[str],
    *,
    max_tags: int = 8,
    policy: TagPolicy | None = None,
) -> SemanticResult:
    """Select existing candidates and optionally one new tag."""
    schema = {
        "type": "object",
        "required": ["tags", "new_tag", "new_tag_justification"],
        "properties": {
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "uniqueItems": True,
                "maxItems": max_tags,
            },
            "new_tag": {"type": ["string", "null"]},
            "new_tag_justification": {"type": ["string", "null"]},
        },
        "additionalProperties": False,
    }
    result = runtime.run(
        SemanticTask(
            operation="obsidian.generate_tags",
            role="tagger",
            facts={"note": document.analysis_content},
            choices=tuple(candidates),
            output_schema=schema,
            content_hash=document.content_hash,
            model_policy={"max_tokens": max(32, max_tags * 8)},
        )
    )
    values = result.value.get("tags", [])
    new_tag = result.value.get("new_tag")
    justification = result.value.get("new_tag_justification")
    if (
        not isinstance(values, list)
        or (new_tag is not None and not isinstance(new_tag, str))
        or (justification is not None and not isinstance(justification, str))
    ):
        raise SemanticError("tagger returned invalid tag fields")
    if new_tag is not None and not justification:
        raise SemanticError("new tag requires explicit semantic justification")
    try:
        normalized_values = [normalize_tag(tag) for tag in values]
        if policy is not None:
            policy.validate(normalized_values)
    except (TypeError, ValueError) as exc:
        raise SemanticError(str(exc)) from exc
    if new_tag is not None:
        normalized_new = normalize_tag(new_tag)
        if not normalized_new:
            raise SemanticError("new tag is empty after normalization")
    else:
        normalized_new = None
    allowed = {normalize_tag(candidate) for candidate in candidates}
    extras = [tag for tag in normalized_values if tag and tag not in allowed]
    if normalized_new is not None and normalized_new not in extras:
        extras.append(normalized_new)
    if len(extras) > 1:
        raise SemanticError("tagger proposed more than one uncontrolled new tag")
    if normalized_new is not None and extras and normalized_new != extras[0]:
        raise SemanticError("new_tag must match the one uncontrolled tag")
    return result


def classify_project(
    runtime: SemanticRuntime,
    document: NoteDocument,
    projects: list[str],
) -> ProjectDecision:
    """Classify a note and route confidence for verification or escalation."""
    schema: dict[str, Any] = {
        "type": "object",
        "required": ["choice", "confidence"],
        "properties": {
            "choice": {"type": "string", "enum": [*projects, "none"]},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        },
        "additionalProperties": False,
    }
    result = runtime.run(
        SemanticTask(
            operation="obsidian.classify_project",
            role="note_classifier",
            facts={"note": document.analysis_content},
            choices=tuple([*projects, "none"]),
            output_schema=schema,
            content_hash=document.content_hash,
            model_policy={"max_tokens": 24, "temperature": 0.0},
        )
    )
    confidence = float(result.value["confidence"])
    route = (
        ConfidenceRoute.ACCEPT
        if confidence > 0.9
        else ConfidenceRoute.VERIFY
        if confidence >= 0.7
        else ConfidenceRoute.RETRIEVE_MORE
        if confidence >= 0.5
        else ConfidenceRoute.ASK_USER
    )
    return ProjectDecision(result.value["choice"], confidence, route, result)


def choose_folder(
    runtime: SemanticRuntime,
    document: NoteDocument,
    folders: list[str],
) -> tuple[str, float, str]:
    """Ask the model to choose one approved destination folder."""
    schema = {
        "type": "object",
        "required": ["folder", "confidence", "justification"],
        "properties": {
            "folder": {"type": "string", "enum": folders},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "justification": {"type": "string"},
        },
        "additionalProperties": False,
    }
    result = runtime.run(
        SemanticTask(
            operation="obsidian.choose_folder",
            role="folder_router",
            facts={"note": document.analysis_content},
            choices=tuple(folders),
            output_schema=schema,
            content_hash=document.content_hash,
            model_policy={"max_tokens": 48, "temperature": 0.0},
        )
    )
    value = result.value
    folder = value.get("folder")
    confidence = value.get("confidence")
    justification = value.get("justification")
    if (
        not isinstance(folder, str)
        or folder not in folders
        or not isinstance(confidence, (int, float))
        or isinstance(confidence, bool)
        or not isinstance(justification, str)
    ):
        raise SemanticError("folder proposal was invalid")
    return folder, float(confidence), justification
