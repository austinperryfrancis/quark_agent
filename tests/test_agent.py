from __future__ import annotations

from pathlib import Path

from quark.agent.agent import Agent
from quark.agent.models import IntentDefinition, RouteKind
from quark.agent.router import IntentRouter
from quark.agent.skills import SkillRegistry, SkillResult
from quark.agent.lifecycle import Proposal, ProposalOutcome, Validation, ValidationMode
from quark.models.provider import ModelResponse
from quark.state.database import StateDatabase


class FakeProvider:
    provider_name = "fake"
    model_name = "tiny"

    def __init__(self, response: str = "Hello from chat.") -> None:
        self.response = response
        self.calls = 0

    def generate(self, prompt: str, **kwargs) -> ModelResponse:
        self.calls += 1
        return ModelResponse(self.response)

    def health_check(self, **kwargs) -> bool:
        return True


class FakeSkill:
    name = "obsidian"
    executions = 0
    applications = 0

    def intents(self):
        return (
            IntentDefinition(
                "obsidian.search",
                self.name,
                "search notes",
                keywords=("notes", "vault"),
            ),
            IntentDefinition(
                "obsidian.organize",
                self.name,
                "organize inbox",
                process=True,
                write=True,
                keywords=("organize", "inbox"),
            ),
        )

    def execute(self, intent, arguments):
        self.executions += 1
        if intent == "obsidian.organize":
            return SkillResult(
                "Preview. Apply?", proposed_action={"note_ids": ["a.md"]}
            )
        return SkillResult("Found a note.")

    def apply(self, intent, payload):
        self.applications += 1
        return SkillResult("Applied.")

    def propose(self, operation, arguments):
        result = self.execute(operation, arguments)
        payload = dict(result.proposed_action or {})
        return Proposal(
            self.name, operation, payload,
            Validation(True, "deterministic"),
            Validation(True, "semantic"),
            ValidationMode.USER if payload else ValidationMode.OFF,
            text=result.text,
        )

    def revise_proposal(self, proposal, feedback):
        return proposal

    def explain_proposal(self, proposal, question):
        return proposal.text

    def apply_proposal(self, proposal):
        self.applications += 1
        return ProposalOutcome("Applied.", {})


def _agent(tmp_path: Path, provider: FakeProvider | None = None):
    state = StateDatabase(tmp_path / "state.db")
    skills = SkillRegistry()
    skill = FakeSkill()
    skills.register(skill)
    return Agent(provider or FakeProvider(), state, skills), state, skill


def test_general_chat_does_not_invoke_skill(tmp_path: Path) -> None:
    agent, state, skill = _agent(tmp_path)
    try:
        result = agent.respond("s", "How are you today?")
        assert result.route.route is RouteKind.CHAT
        assert result.text == "Hello from chat."
        assert skill.executions == 0
        assert len(state.recent_messages("s")) == 2
    finally:
        state.close()


def test_skill_query_and_process_confirmation(tmp_path: Path) -> None:
    agent, state, skill = _agent(tmp_path)
    try:
        query = agent.respond("s", "find my notes")
        assert query.route.route is RouteKind.SKILL_QUERY
        preview = agent.respond("s", "organize")
        assert preview.pending_confirmation
        assert state.pending_action("s") is not None
        applied = agent.respond("s", "yes")
        assert applied.route.route is RouteKind.CONTINUE_PROCESS
        assert skill.applications == 1
        assert state.pending_action("s") is None
    finally:
        state.close()


def test_invalid_or_low_confidence_router_output_clarifies() -> None:
    intents = (
        IntentDefinition("one", "x", "one", keywords=("do",)),
        IntentDefinition("two", "x", "two", keywords=("do",)),
    )
    provider = FakeProvider('{"intent":"one","confidence":0.2}')
    assert IntentRouter(provider).route("do it", intents).route is RouteKind.CLARIFY
    provider.response = "not json"
    assert IntentRouter(provider).route("do it", intents).route is RouteKind.CLARIFY
