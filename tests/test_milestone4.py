from __future__ import annotations

from pathlib import Path

import pytest

from quark.config import RetryConfig
from quark.core.context import SemanticTask, compile_context
from quark.models.provider import ModelProviderError, ModelResponse
from quark.models.semantic import SemanticError, SemanticRuntime
from quark.state.database import StateDatabase


class FakeProvider:
    provider_name = "fake"
    model_name = "tiny"

    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def generate(self, prompt: str, **kwargs) -> ModelResponse:
        self.calls.append((prompt, kwargs))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return ModelResponse(response)

    def health_check(self, **kwargs) -> bool:
        return True


SCHEMA = {
    "type": "object",
    "required": ["choice"],
    "properties": {"choice": {"type": "string", "enum": ["research", "personal"]}},
    "additionalProperties": False,
}


def task() -> SemanticTask:
    return SemanticTask(
        operation="text.classify",
        role="note_classifier",
        facts={"note_summary": "A research note about sanctions."},
        choices=("research", "personal"),
        output_schema=SCHEMA,
        content_hash="note-hash",
        model_policy={"temperature": 0.0, "max_tokens": 20},
        max_input_tokens=120,
    )


def test_context_compiler_is_compact_and_constrained() -> None:
    prompt = compile_context(task(), "Classify the note.")
    assert len(prompt) <= 480
    assert "note_summary" in prompt
    assert "OUTPUT JSON SCHEMA" in prompt
    assert "Return JSON only" in prompt


def test_semantic_runtime_validates_structured_output_and_records_cache(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(['{"choice":"research"}'])
    with StateDatabase(tmp_path / "state.db") as state:
        runtime = SemanticRuntime(
            provider,
            state=state,
            retries=RetryConfig(initial_delay_seconds=0),
            prompt_root=Path("skills/obsidian/prompts"),
        )
        first = runtime.run(task())
        second = runtime.run(task())
        assert first.value == {"choice": "research"}
        assert first.attempts == 1 and not first.cached
        assert second.cached and second.attempts == 0
        assert len(provider.calls) == 1
        snapshot = state.inspect_goal(999)
        assert snapshot is None


def test_semantic_runtime_repairs_invalid_json_then_succeeds() -> None:
    provider = FakeProvider(["not json", '```json\n{"choice":"personal"}\n```'])
    runtime = SemanticRuntime(
        provider,
        retries=RetryConfig(max_attempts=2, initial_delay_seconds=0),
        prompt_root=Path("skills/obsidian/prompts"),
    )

    result = runtime.run(task())

    assert result.value == {"choice": "personal"}
    assert result.attempts == 2
    assert "REPAIR ATTEMPT 2" in provider.calls[1][0]


@pytest.mark.parametrize(
    "responses",
    [
        ['{"choice":"unknown"}', '{"choice":"unknown"}'],
        [ModelProviderError("offline"), ModelProviderError("offline")],
    ],
)
def test_semantic_runtime_exhaustion_is_explicit(responses) -> None:
    provider = FakeProvider(responses)
    runtime = SemanticRuntime(
        provider,
        retries=RetryConfig(max_attempts=2, initial_delay_seconds=0),
        prompt_root=Path("skills/obsidian/prompts"),
    )

    with pytest.raises(SemanticError, match="failed after 2 attempts"):
        runtime.run(task())


def test_model_provider_health_and_generation_are_provider_neutral() -> None:
    provider = FakeProvider(['{"choice":"research"}'])
    assert provider.health_check()
    response = provider.generate("prompt", schema=SCHEMA, max_tokens=10)
    assert response.text.startswith("{")
