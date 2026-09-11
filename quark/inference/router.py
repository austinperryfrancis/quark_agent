"""Root routing: one user request to one installed top-level Skill."""

from __future__ import annotations

from quark.inference.models import Message, MessageRole, RouteDecision
from quark.inference.prompts.route_skill_v1 import build_route_prompt
from quark.inference.provider import ModelProvider
from quark.skills.registry import SkillRegistry


class RoutingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class RootRouter:
    """Ask the model only which top-level Skill owns a new request."""

    def __init__(self, registry: SkillRegistry, provider: ModelProvider) -> None:
        self.registry = registry
        self.provider = provider

    async def route(self, request: str) -> RouteDecision:
        if not request.strip():
            raise ValueError("request cannot be empty")

        skills = self.registry.top_level()
        if not skills:
            return RouteDecision(unsupported=True)

        decision = await self.provider.generate_structured(
            (
                Message(
                    role=MessageRole.USER,
                    content=build_route_prompt(request, skills),
                ),
            ),
            RouteDecision,
        )
        if decision.skill is not None and decision.skill not in {
            skill.name for skill in skills
        }:
            raise RoutingError(
                "INVALID_ROUTE_SELECTION",
                f"Model selected unavailable top-level Skill {decision.skill!r}.",
            )
        return decision
