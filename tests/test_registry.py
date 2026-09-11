from pydantic import BaseModel
import pytest

from quark.skills import (
    DuplicateSkillError,
    InvalidSkillGraphError,
    Skill,
    SkillContext,
    SkillRegistry,
)
from quark.skills.registry import InvalidSkillError, SkillNotFoundError


class Empty(BaseModel):
    pass


class ExampleSkill(Skill):
    name = "test.example"
    description = "An example."
    input_schema = Empty
    output_schema = Empty
    top_level = True

    async def run(self, ctx: SkillContext, args: BaseModel) -> Empty:
        return Empty()


def test_register_and_inspect_skill() -> None:
    registry = SkillRegistry()
    registry.register(ExampleSkill())

    assert registry.get("test.example").name == "test.example"
    assert registry.metadata("test.example").description == "An example."
    assert [item.name for item in registry.top_level()] == ["test.example"]


def test_duplicate_skill_names_are_rejected() -> None:
    registry = SkillRegistry()
    registry.register(ExampleSkill())

    with pytest.raises(DuplicateSkillError):
        registry.register(ExampleSkill())


def test_invalid_or_unknown_names_are_rejected() -> None:
    registry = SkillRegistry()
    skill = ExampleSkill()
    skill.name = "NotNamespaced"

    with pytest.raises(InvalidSkillError):
        registry.register(skill)
    with pytest.raises(SkillNotFoundError):
        registry.get("test.missing")


def test_registry_rejects_missing_child_references() -> None:
    registry = SkillRegistry()
    skill = ExampleSkill()
    skill.allowed_children = ("test.missing",)
    registry.register(skill)

    with pytest.raises(InvalidSkillGraphError):
        registry.validate_graph()
