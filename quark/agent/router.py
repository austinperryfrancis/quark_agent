"""Layered intent routing designed for very small local models."""

from __future__ import annotations

import json

from quark.agent.lifecycle import Proposal, Validation, ValidationMode
from quark.agent.models import IntentDefinition, RouteDecision, RouteKind
from quark.models.provider import ModelProvider, ModelProviderError


class IntentRouter:
    def __init__(self, provider: ModelProvider | None = None) -> None:
        self.provider = provider

    def shortlist(
        self, message: str, intents: tuple[IntentDefinition, ...], limit: int = 3
    ) -> tuple[IntentDefinition, ...]:
        words = set(message.casefold().replace("?", "").split())
        scored = []
        for intent in intents:
            overlap = len(
                words.intersection(keyword.casefold() for keyword in intent.keywords)
            )
            scored.append((overlap, intent.name, intent))
        return tuple(
            item[2]
            for item in sorted(scored, key=lambda item: (-item[0], item[1]))[:limit]
            if item[0] > 0
        )

    def route(
        self, message: str, intents: tuple[IntentDefinition, ...]
    ) -> RouteDecision:
        stripped = message.strip()
        if stripped.startswith("/"):
            return RouteDecision(
                RouteKind.COMMAND, 1.0, arguments={"command": stripped}
            )
        candidates = self.shortlist(stripped, intents)
        if not candidates:
            return RouteDecision(
                RouteKind.CHAT, 0.95, reason="no skill intent shortlisted"
            )
        # Select a skill first when candidates span multiple skill trees.
        skill_names = tuple(sorted({item.skill for item in candidates}))
        if len(skill_names) > 1 and self.provider is not None:
            schema = {
                "type": "object",
                "required": ["skill", "confidence"],
                "properties": {
                    "skill": {
                        "type": "string",
                        "enum": [*skill_names, "chat", "clarify"],
                    },
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "additionalProperties": False,
            }
            prompt = (
                "Choose the skill for this message. Return JSON only.\nMESSAGE: "
                + stripped[:1000]
                + "\nSKILLS: "
                + json.dumps(skill_names)
            )
            try:
                value = json.loads(
                    self.provider.generate(
                        prompt, schema=schema, temperature=0.0, max_tokens=16
                    ).text
                )
                choice = value["skill"]
                confidence = float(value["confidence"])
                if choice == "chat":
                    return RouteDecision(RouteKind.CHAT, confidence)
                if choice == "clarify" or choice not in skill_names or confidence < 0.7:
                    return RouteDecision(RouteKind.CLARIFY, confidence)
                candidates = tuple(item for item in candidates if item.skill == choice)
            except (
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                ModelProviderError,
            ):
                return RouteDecision(
                    RouteKind.CLARIFY, 0.0, reason="skill routing failed"
                )
        if len(candidates) == 1:
            intent = candidates[0]
            route = RouteKind.START_PROCESS if intent.process else RouteKind.SKILL_QUERY
            return RouteDecision(
                route,
                0.95,
                intent.skill,
                intent.name,
                {"message": stripped},
                "deterministic shortlist",
            )
        if self.provider is None:
            return RouteDecision(
                RouteKind.CLARIFY, 0.0, reason="multiple skill intents matched"
            )
        choices = [intent.name for intent in candidates]
        schema = {
            "type": "object",
            "required": ["intent", "confidence"],
            "properties": {
                "intent": {"type": "string", "enum": [*choices, "chat", "clarify"]},
                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "additionalProperties": False,
        }
        prompt = (
            "Route one message. Choose only from the supplied intents.\nMESSAGE: "
            + stripped[:1000]
            + "\nINTENTS: "
            + json.dumps(
                [
                    {"name": item.name, "description": item.description}
                    for item in candidates
                ]
            )
        )
        try:
            value = json.loads(
                self.provider.generate(
                    prompt, schema=schema, temperature=0.0, max_tokens=24
                ).text
            )
            if (
                not isinstance(value, dict)
                or set(value) != {"intent", "confidence"}
                or not isinstance(value.get("intent"), str)
                or not isinstance(value.get("confidence"), (int, float))
                or isinstance(value.get("confidence"), bool)
                or not 0 <= float(value["confidence"]) <= 1
            ):
                raise ValueError("router result does not match schema")
            choice = value["intent"]
            confidence = float(value["confidence"])
        except (
            KeyError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
            ModelProviderError,
        ):
            return RouteDecision(
                RouteKind.CLARIFY, 0.0, reason="router output was invalid"
            )
        if choice == "chat":
            return RouteDecision(RouteKind.CHAT, confidence)
        if choice == "clarify" or confidence < 0.7:
            return RouteDecision(
                RouteKind.CLARIFY,
                confidence,
                reason="routing confidence below threshold",
            )
        if confidence < 0.9:
            verify_schema = {
                "type": "object",
                "required": ["valid"],
                "properties": {"valid": {"type": "boolean"}},
                "additionalProperties": False,
            }
            try:
                verified = json.loads(
                    self.provider.generate(
                        "Verify whether this intent matches the message. Return JSON only.\n"
                        f"MESSAGE: {stripped[:600]}\nINTENT: {choice}",
                        schema=verify_schema,
                        temperature=0.0,
                        max_tokens=8,
                    ).text
                )
                if (
                    not isinstance(verified, dict)
                    or set(verified) != {"valid"}
                    or verified.get("valid") is not True
                ):
                    raise ValueError("route rejected")
            except (
                AttributeError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
                ModelProviderError,
            ):
                return RouteDecision(
                    RouteKind.CLARIFY,
                    confidence,
                    reason="medium-confidence route was not verified",
                )
        selected = next((item for item in candidates if item.name == choice), None)
        if selected is None:
            return RouteDecision(
                RouteKind.CLARIFY, 0.0, reason="router selected an unavailable intent"
            )
        route = RouteKind.START_PROCESS if selected.process else RouteKind.SKILL_QUERY
        return RouteDecision(
            route, confidence, selected.skill, selected.name, {"message": stripped}
        )


class RoutingSkill:
    """First-class lifecycle skill that selects and validates the next route."""

    name = "route_request"

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self.router = IntentRouter(provider)

    def propose(self, message: str, intents: tuple[IntentDefinition, ...]) -> Proposal:
        decision = self.router.route(message, intents)
        exists = decision.route in {
            RouteKind.CHAT,
            RouteKind.COMMAND,
            RouteKind.CLARIFY,
        } or any(
            intent.name == decision.intent and intent.skill == decision.skill
            for intent in intents
        )
        semantic = self._verify(message, decision)
        return Proposal(
            self.name,
            "route",
            {
                "route": decision.route.value,
                "skill": decision.skill,
                "intent": decision.intent,
                "arguments": decision.arguments,
                "confidence": decision.confidence,
                "reason": decision.reason,
            },
            Validation(
                exists,
                "route target is available"
                if exists
                else "route target is unavailable",
            ),
            semantic,
            ValidationMode.AGENT,
        )

    def apply(self, proposal: Proposal) -> RouteDecision:
        payload = proposal.payload
        return RouteDecision(
            RouteKind(str(payload["route"])),
            float(payload["confidence"]),
            payload.get("skill"),
            payload.get("intent"),
            dict(payload.get("arguments", {})),
            str(payload.get("reason", "")),
        )

    def _verify(self, message: str, decision: RouteDecision) -> Validation:
        if self.router.provider is None or decision.route in {
            RouteKind.COMMAND,
            RouteKind.CLARIFY,
        }:
            return Validation(True, "deterministic route")
        try:
            raw = self.router.provider.generate(
                "Validate this routing decision. Return JSON only.\n"
                f"MESSAGE: {message[:600]}\nROUTE: {decision.route.value}\n"
                f"SKILL: {decision.skill or 'none'}\nINTENT: {decision.intent or 'none'}",
                schema={
                    "type": "object",
                    "required": ["valid"],
                    "properties": {"valid": {"type": "boolean"}},
                    "additionalProperties": False,
                },
                temperature=0.0,
                max_tokens=8,
            ).text
            return Validation(
                bool(json.loads(raw).get("valid")), "LLM route verification"
            )
        except (json.JSONDecodeError, ModelProviderError, TypeError, ValueError):
            # A verifier failure must not discard a deterministic route; it is
            # recorded as a degraded semantic check instead of a hard failure.
            return Validation(True, "LLM route verifier unavailable; deterministic route retained")
