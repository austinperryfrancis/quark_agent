import asyncio
from typing import Any

from pydantic import BaseModel
import pytest

from quark.inference import Message, ModelProvider, RootRouter, RouteDecision, RoutingError
from quark.inference.prompts import ROUTE_SKILL_PROMPT_VERSION
from quark.skills import Skill, SkillContext, SkillRegistry


class Empty(BaseModel):
    pass


class RoutingSkill(Skill):
    description = "Handle routing tests."
    input_schema = Empty
    output_schema = Empty

    async def run(self, ctx: SkillContext, args: BaseModel) -> Empty:
        return Empty()


class FakeProvider(ModelProvider):
    def __init__(self, decision: RouteDecision) -> None:
        self.decision = decision
        self.calls: list[tuple[tuple[Message, ...], type[BaseModel], dict | None]] = []

    async def generate_structured(
        self,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
        options: dict[str, Any] | None = None,
    ) -> BaseModel:
        self.calls.append((messages, schema, options))
        return self.decision


def registry_with_routes() -> SkillRegistry:
    registry = SkillRegistry()
    top = RoutingSkill()
    top.name = "notes.organize"
    top.description = "Organize a note."
    top.top_level = True
    child = RoutingSkill()
    child.name = "notes.read"
    child.description = "Read note contents."
    child.top_level = False
    registry.register(top)
    registry.register(child)
    return registry


def test_root_router_selects_one_top_level_skill_with_minimal_context() -> None:
    provider = FakeProvider(RouteDecision(skill="notes.organize"))
    router = RootRouter(registry_with_routes(), provider)

    decision = asyncio.run(router.route("Organize my newest note."))

    messages, schema, options = provider.calls[0]
    prompt = messages[0].content
    assert decision.skill == "notes.organize"
    assert schema is RouteDecision
    assert options is None
    assert len(messages) == 1
    assert "notes.organize" in prompt
    assert "Organize a note." in prompt
    assert "notes.read" not in prompt
    assert "input_schema" not in prompt
    assert ROUTE_SKILL_PROMPT_VERSION == "route_skill.v1"


def test_root_router_returns_unsupported_without_calling_model_when_empty() -> None:
    provider = FakeProvider(RouteDecision(skill="anything.invalid"))
    router = RootRouter(SkillRegistry(), provider)

    decision = asyncio.run(router.route("Do something."))

    assert decision.unsupported
    assert provider.calls == []


def test_root_router_accepts_model_unsupported_decision() -> None:
    provider = FakeProvider(RouteDecision(unsupported=True))
    router = RootRouter(registry_with_routes(), provider)

    decision = asyncio.run(router.route("Fly me to Mars."))

    assert decision.unsupported


def test_root_router_rejects_unknown_or_non_top_level_selection() -> None:
    for selected in ("notes.read", "notes.missing"):
        provider = FakeProvider(RouteDecision(skill=selected))
        router = RootRouter(registry_with_routes(), provider)

        with pytest.raises(RoutingError) as caught:
            asyncio.run(router.route("Read a note."))

        assert caught.value.code == "INVALID_ROUTE_SELECTION"
