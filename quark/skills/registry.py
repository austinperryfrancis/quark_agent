"""A small explicit registry of installed Skills."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from pydantic import BaseModel

from quark.models import ReviewPolicy, SideEffect
from quark.skills.base import Skill

_SKILL_NAME = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class DuplicateSkillError(ValueError):
    pass


class InvalidSkillError(ValueError):
    pass


class InvalidSkillGraphError(ValueError):
    pass


class SkillNotFoundError(LookupError):
    pass


@dataclass(frozen=True, slots=True)
class SkillMetadata:
    name: str
    description: str
    input_schema: type[BaseModel]
    output_schema: type[BaseModel]
    allowed_children: tuple[str, ...]
    side_effect: SideEffect
    review_policy: ReviewPolicy
    top_level: bool


class SkillRegistry:
    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        if not _SKILL_NAME.fullmatch(skill.name):
            raise InvalidSkillError(
                f"Skill name {skill.name!r} must use a dotted lowercase namespace"
            )
        if skill.name in self._skills:
            raise DuplicateSkillError(f"Skill {skill.name!r} is already registered")
        if skill.max_child_calls < 0:
            raise InvalidSkillError("max_child_calls cannot be negative")
        if len(skill.allowed_children) != len(set(skill.allowed_children)):
            raise InvalidSkillError("allowed_children cannot contain duplicates")
        self._skills[skill.name] = skill

    def validate_graph(self) -> None:
        """Reject child capabilities that are not installed in this registry."""
        missing = {
            (skill.name, child)
            for skill in self._skills.values()
            for child in skill.allowed_children
            if child not in self._skills
        }
        if missing:
            references = ", ".join(
                f"{parent} -> {child}" for parent, child in sorted(missing)
            )
            raise InvalidSkillGraphError(
                f"Skill graph references unregistered children: {references}"
            )

    def get(self, name: str) -> Skill:
        try:
            return self._skills[name]
        except KeyError as exc:
            raise SkillNotFoundError(f"Unknown Skill {name!r}") from exc

    def metadata(self, name: str) -> SkillMetadata:
        skill = self.get(name)
        return SkillMetadata(
            name=skill.name,
            description=skill.description,
            input_schema=skill.input_schema,
            output_schema=skill.output_schema,
            allowed_children=skill.allowed_children,
            side_effect=skill.side_effect,
            review_policy=skill.review_policy,
            top_level=skill.top_level,
        )

    def top_level(self) -> tuple[SkillMetadata, ...]:
        return tuple(
            self.metadata(name)
            for name in sorted(self._skills)
            if self._skills[name].top_level
        )

    def __contains__(self, name: object) -> bool:
        return name in self._skills

    def __iter__(self) -> Iterator[Skill]:
        return iter(self._skills.values())

    def __len__(self) -> int:
        return len(self._skills)
