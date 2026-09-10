"""Conversation coordinator for chat, skill queries, and known processes."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, cast

from quark.agent.lifecycle import (
    FeedbackKind,
    classify_feedback,
    proposal_from_payload,
    proposal_payload,
    review_required,
)
from quark.agent.models import AgentResponse, RouteDecision, RouteKind
from quark.agent.router import IntentRouter, RoutingSkill
from quark.agent.skills import SkillRegistry
from quark.models.provider import ModelProvider, ModelProviderError
from quark.state.database import StateDatabase


class Agent:
    def __init__(
        self,
        provider: ModelProvider,
        state: StateDatabase,
        skills: SkillRegistry,
        router: IntentRouter | None = None,
    ) -> None:
        self.provider = provider
        self.state = state
        self.skills = skills
        self.router = router or IntentRouter(provider)
        self.routing_skill = RoutingSkill(provider)

    def respond(
        self,
        session_id: str,
        message: str,
        on_token: Callable[[str], None] | None = None,
    ) -> AgentResponse:
        self.state.add_message(session_id, "user", message)
        pending = self.state.pending_action(session_id)
        if message.strip().startswith("/"):
            decision = self.router.route(message, self.skills.intents())
            response = self._dispatch(session_id, message, decision, on_token)
        elif pending is not None:
            response = self._continue_pending(session_id, message, pending)
        else:
            route_proposal = self.routing_skill.propose(message, self.skills.intents())
            decision = self.routing_skill.apply(route_proposal)
            if (
                not route_proposal.deterministic.valid
                or not route_proposal.semantic.valid
            ):
                decision = RouteDecision(
                    RouteKind.CLARIFY, 0.0, reason="routing validation failed"
                )
            self.state.record_route(
                session_id,
                decision.route,
                decision.skill,
                decision.intent,
                decision.confidence,
            )
            response = self._dispatch(session_id, message, decision, on_token)
        self.state.add_message(session_id, "assistant", response.text)
        return response

    def _dispatch(
        self,
        session_id: str,
        message: str,
        decision: RouteDecision,
        on_token: Callable[[str], None] | None = None,
    ) -> AgentResponse:
        if decision.route is RouteKind.COMMAND:
            return self._command(
                session_id, str(decision.arguments["command"]), decision
            )
        if decision.route is RouteKind.CLARIFY:
            return AgentResponse(
                "I’m not confident which action you want. Could you be more specific?",
                decision,
            )
        if decision.route is RouteKind.CHAT:
            context = self.state.recent_messages(session_id, 6)
            prompt = (
                "You are Quark, a concise local personal assistant. Answer conversationally.\n"
                + "\n".join(f"{row['role']}: {row['content']}" for row in context)
            )
            try:
                stream = getattr(self.provider, "generate_stream", None)
                if on_token is not None and callable(stream):
                    chunks: list[str] = []
                    for chunk in stream(prompt, temperature=0.2, max_tokens=256):
                        chunks.append(chunk)
                        on_token(chunk)
                    text = "".join(chunks).strip()
                else:
                    text = self.provider.generate(
                        prompt, temperature=0.2, max_tokens=256
                    ).text.strip()
                    if on_token is not None:
                        on_token(text)
            except ModelProviderError as error:
                text = f"The local model is unavailable: {error}"
            return AgentResponse(text, decision)
        if decision.skill is None or decision.intent is None:
            return AgentResponse(
                "The selected route did not identify a usable skill.", decision
            )
        skill = self.skills.get(decision.skill)
        propose = getattr(skill, "propose", None)
        if callable(propose):
            proposal = propose(decision.intent, decision.arguments)
            if not review_required(proposal):
                apply_proposal = getattr(skill, "apply_proposal", None)
                if callable(apply_proposal):
                    outcome = apply_proposal(proposal)
                    if hasattr(outcome, "text"):
                        return AgentResponse(
                            outcome.text, decision, False, outcome.data
                        )
                    return AgentResponse(
                        str(outcome.get("text", "")),
                        decision,
                        False,
                        dict(outcome.get("data", {})),
                    )
            pending_payload = proposal_payload(proposal)
            self.state.create_pending_action(
                session_id, decision.skill, decision.intent, pending_payload
            )
            self.state.record_skill_invocation(
                session_id, decision.skill, decision.intent, "preview", proposal.payload
            )
            return AgentResponse(proposal.text, decision, True, proposal.payload)
        return AgentResponse(
            "The selected skill does not implement the lifecycle contract.", decision
        )

    def _continue_pending(
        self, session_id: str, message: str, pending: dict[str, object]
    ) -> AgentResponse:
        feedback = classify_feedback(message)
        accepted = feedback is FeedbackKind.APPROVE
        decision = RouteDecision(
            RouteKind.CONTINUE_PROCESS,
            1.0,
            str(pending["skill"]),
            str(pending["intent"]),
        )
        cancelled = feedback is FeedbackKind.CANCEL
        if cancelled:
            self.state.resolve_pending_action(cast(int, pending["id"]), "cancelled")
            return AgentResponse("Cancelled. No changes were applied.", decision)
        if not accepted:
            skill = self.skills.get(str(pending["skill"]))
            if "_proposal" in cast(dict[str, Any], pending["payload"]):
                revise_proposal = getattr(skill, "revise_proposal", None)
                if callable(revise_proposal):
                    proposal = proposal_from_payload(
                        cast(dict[str, Any], pending["payload"])
                    )
                    if feedback is FeedbackKind.SHOW:
                        return AgentResponse(
                            proposal.text, decision, True, proposal.payload
                        )
                    if feedback is FeedbackKind.EXPLAIN:
                        explain = getattr(skill, "explain_proposal", None)
                        if callable(explain):
                            return AgentResponse(
                                explain(proposal, message),
                                decision,
                                True,
                                proposal.payload,
                            )
                        return AgentResponse(
                            "This is the current proposal:\n" + proposal.text,
                            decision,
                            True,
                            proposal.payload,
                        )
                    revised = revise_proposal(proposal, message)
                    self.state.resolve_pending_action(
                        cast(int, pending["id"]), "revised"
                    )
                    self.state.create_pending_action(
                        session_id,
                        str(pending["skill"]),
                        str(pending["intent"]),
                        proposal_payload(revised),
                    )
                    return AgentResponse(revised.text, decision, True, revised.payload)
            return AgentResponse(
                "This proposal cannot be revised because it is not a lifecycle proposal.",
                decision,
            )
        skill = self.skills.get(str(pending["skill"]))
        if "_proposal" in cast(dict[str, Any], pending["payload"]):
            apply_proposal = getattr(skill, "apply_proposal", None)
            if callable(apply_proposal):
                proposal = proposal_from_payload(
                    cast(dict[str, Any], pending["payload"])
                )
                outcome = apply_proposal(proposal)
                if hasattr(outcome, "text"):
                    self.state.resolve_pending_action(
                        cast(int, pending["id"]), "applied"
                    )
                    return AgentResponse(
                        outcome.text,
                        decision,
                        bool(outcome.next_proposal),
                        outcome.data,
                    )
                self.state.resolve_pending_action(cast(int, pending["id"]), "applied")
                next_payload = outcome.get("next")
                if isinstance(next_payload, dict):
                    self.state.create_pending_action(
                        session_id,
                        str(pending["skill"]),
                        str(pending["intent"]),
                        next_payload,
                    )
                return AgentResponse(
                    str(outcome.get("text", "Applied.")),
                    decision,
                    isinstance(next_payload, dict),
                    dict(outcome.get("data", {})),
                )
        self.state.resolve_pending_action(cast(int, pending["id"]), "failed")
        return AgentResponse(
            "The pending proposal used an unsupported lifecycle format and was cleared.",
            decision,
        )

    def _command(
        self, session_id: str, command: str, decision: RouteDecision
    ) -> AgentResponse:
        name = command.split()[0].casefold()
        if name == "/skills":
            return AgentResponse(
                "Available skills: " + ", ".join(self.skills.names()), decision
            )
        if name == "/status":
            pending = self.state.pending_action(session_id)
            return AgentResponse(
                "Pending action: " + (str(pending["intent"]) if pending else "none"),
                decision,
            )
        if name == "/proposal":
            pending = self.state.pending_action(session_id)
            if pending is None:
                return AgentResponse("No active proposal.", decision)
            try:
                proposal = proposal_from_payload(
                    cast(dict[str, Any], pending["payload"])
                )
            except ValueError:
                return AgentResponse(
                    "The active proposal uses the legacy format.", decision
                )
            return AgentResponse(proposal.text, decision, data=proposal.payload)
        if name == "/clear":
            pending = self.state.pending_action(session_id)
            if pending is not None:
                self.state.resolve_pending_action(cast(int, pending["id"]), "cancelled")
            with self.state.transaction() as connection:
                connection.execute(
                    "DELETE FROM messages WHERE session_id=?", (session_id,)
                )
            return AgentResponse("Conversation cleared.", decision)
        return AgentResponse(
            "Commands: /help, /status, /proposal, /skills, /clear, /exit", decision
        )
