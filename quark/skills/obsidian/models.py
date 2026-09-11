"""Typed contracts for the initial Obsidian Skills."""

from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator

_TAG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NotePathInput(StrictModel):
    path: str = Field(min_length=1)


class NoteContent(StrictModel):
    path: str
    content: str
    tags: list[str] = Field(default_factory=list)


class InboxLatestInput(StrictModel):
    pass


class InboxLatestResult(StrictModel):
    path: str


class ProposeTagsInput(StrictModel):
    path: str
    content: str = Field(max_length=100_000)
    existing_tags: list[str] = Field(default_factory=list)


class TagProposal(StrictModel):
    tags: list[str] = Field(min_length=1, max_length=5)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in tags:
            value = "-".join(
                tag.strip().lower().lstrip("#").replace("_", " ").split()
            )
            if not value:
                raise ValueError("tags cannot be empty")
            if not _TAG_PATTERN.fullmatch(value):
                raise ValueError("tags must use lowercase kebab-case words")
            if value in normalized:
                raise ValueError("tags must be unique after normalization")
            normalized.append(value)
        return normalized


class ApplyTagsInput(StrictModel):
    path: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1, max_length=5)

    _normalize_tags = field_validator("tags")(TagProposal.normalize_tags.__func__)


class ApplyTagsResult(StrictModel):
    path: str
    tags: list[str]
    changed: bool


class OrganizeNoteInput(StrictModel):
    path: str | None = Field(
        default=None,
        description=(
            "Explicit Markdown note path supplied by the user. Use null when the "
            "user asks for the latest or newest inbox note; never invent a path."
        ),
    )


class OrganizeNoteResult(StrictModel):
    path: str
    tags: list[str]
    changed: bool
