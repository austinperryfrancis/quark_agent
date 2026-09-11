"""Typed contracts for the initial Obsidian Skills."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    tags: list[str] = Field(min_length=1, max_length=12)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, tags: list[str]) -> list[str]:
        normalized: list[str] = []
        for tag in tags:
            value = "-".join(tag.strip().lower().lstrip("#").split())
            if not value:
                raise ValueError("tags cannot be empty")
            if value not in normalized:
                normalized.append(value)
        return normalized


class ApplyTagsInput(StrictModel):
    path: str = Field(min_length=1)
    tags: list[str] = Field(min_length=1, max_length=12)

    _normalize_tags = field_validator("tags")(TagProposal.normalize_tags.__func__)


class ApplyTagsResult(StrictModel):
    path: str
    tags: list[str]
    changed: bool


class OrganizeNoteInput(StrictModel):
    path: str | None = None


class OrganizeNoteResult(StrictModel):
    path: str
    tags: list[str]
    changed: bool
