from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.metrics import score_predictions
from quark.config import RetryConfig
from quark.models.provider import ModelResponse
from quark.models.semantic import SemanticError, SemanticRuntime
from quark.state.database import SCHEMA_VERSION, StateDatabase
from skills.obsidian.operations.read_note import read_note
from skills.obsidian.related import render_related_section, validate_related_ids
from skills.obsidian.retrieval import NoteIndex
from skills.obsidian.semantic import (
    ConfidenceRoute,
    classify_project,
    generate_tags,
    verify_project_decision,
)
from skills.obsidian.taxonomy import TagPolicy, TagTaxonomy, normalize_tag


class FakeProvider:
    provider_name = "fake"
    model_name = "tiny"

    def __init__(self, response: str):
        self.response = response
        self.calls = 0

    def generate(self, prompt: str, **kwargs) -> ModelResponse:
        self.calls += 1
        return ModelResponse(self.response)

    def health_check(self, **kwargs) -> bool:
        return True


def test_tag_normalization_and_persistent_vault_sync(
    vault_path: Path, tmp_path: Path
) -> None:
    assert normalize_tag("#Research_Project") == "research-project"
    assert normalize_tag(" research project ") == "research-project"
    with StateDatabase(tmp_path / "state.db") as state:
        taxonomy = TagTaxonomy(state, aliases={"sanction": "sanctions"})
        observed = taxonomy.sync_vault(vault_path)
        rows = taxonomy.state.list_tag_index()
        candidates = taxonomy.candidates("Advisor research about sanctions regressions")

    assert observed > 0
    assert SCHEMA_VERSION == 5
    assert {row["canonical_tag"] for row in rows} >= {"research", "sanctions"}
    assert "sanctions" in candidates


def test_taxonomy_preserves_aliases_and_rebuilds_without_double_counting(
    vault_path: Path, tmp_path: Path
) -> None:
    (vault_path / "Inbox" / "Alias.md").write_text(
        "---\ntags: [sanction]\n---\n#sanction\n", encoding="utf-8"
    )
    with StateDatabase(tmp_path / "state.db") as state:
        taxonomy = TagTaxonomy(state, aliases={"sanction": "sanctions"})
        taxonomy.sync_vault(vault_path)
        first = {
            row["canonical_tag"]: row["frequency"] for row in state.list_tag_index()
        }
        taxonomy.sync_vault(vault_path)
        second = {
            row["canonical_tag"]: row["frequency"] for row in state.list_tag_index()
        }

    assert second["sanctions"] == first["sanctions"]


def test_generate_tags_allows_candidates_and_at_most_one_new_tag(
    vault_path: Path,
) -> None:
    document = read_note(vault_path, "Inbox/Meeting Note.md")
    provider = FakeProvider(
        '{"tags":["research","new-concept"],"new_tag":"new-concept",'
        '"new_tag_justification":"Distinct concept in note"}'
    )
    runtime = SemanticRuntime(
        provider,
        retries=RetryConfig(max_attempts=1),
        prompt_root=Path("skills/obsidian/prompts"),
    )

    result = generate_tags(runtime, document, ["research", "sanctions"])

    assert result.value["tags"] == ["research", "new-concept"]
    assert provider.calls == 1


def test_generate_tags_rejects_uncontrolled_tag_proliferation(vault_path: Path) -> None:
    document = read_note(vault_path, "Inbox/Meeting Note.md")
    provider = FakeProvider(
        '{"tags":["one","two"],"new_tag":null,"new_tag_justification":null}'
    )
    runtime = SemanticRuntime(
        provider,
        retries=RetryConfig(max_attempts=1),
        prompt_root=Path("skills/obsidian/prompts"),
    )

    with pytest.raises(SemanticError, match="more than one"):
        generate_tags(runtime, document, ["research"])


@pytest.mark.parametrize(
    ("response", "route"),
    [
        ('{"choice":"sanctions-paper","confidence":0.95}', ConfidenceRoute.ACCEPT),
        ('{"choice":"sanctions-paper","confidence":0.8}', ConfidenceRoute.VERIFY),
        (
            '{"choice":"sanctions-paper","confidence":0.6}',
            ConfidenceRoute.RETRIEVE_MORE,
        ),
        ('{"choice":"none","confidence":0.2}', ConfidenceRoute.ASK_USER),
    ],
)
def test_project_classification_routes_confidence(
    vault_path: Path, response: str, route: ConfidenceRoute
) -> None:
    document = read_note(vault_path, "Inbox/Meeting Note.md")
    runtime = SemanticRuntime(
        FakeProvider(response),
        retries=RetryConfig(max_attempts=1),
        prompt_root=Path("skills/obsidian/prompts"),
    )

    decision = classify_project(runtime, document, ["sanctions-paper", "ceo-dataset"])

    assert decision.route is route
    assert decision.confidence >= 0


def test_medium_confidence_project_is_verified_by_second_role(vault_path: Path) -> None:
    document = read_note(vault_path, "Inbox/Meeting Note.md")
    provider = FakeProvider('{"choice":"sanctions-paper","confidence":0.8}')
    runtime = SemanticRuntime(
        provider,
        retries=RetryConfig(max_attempts=1),
        prompt_root=Path("skills/obsidian/prompts"),
    )
    decision = classify_project(runtime, document, ["sanctions-paper"])
    provider.response = '{"valid":true}'

    assert verify_project_decision(runtime, document, decision)
    assert provider.calls == 2


def test_tag_policy_enforces_protected_forbidden_and_count() -> None:
    policy = TagPolicy(protected=["project"], forbidden=["private"], max_tags=2)
    assert policy.validate(["Project", "research", "project"]) == (
        "project",
        "research",
    )
    with pytest.raises(ValueError, match="forbidden"):
        policy.validate(["project", "private"])
    with pytest.raises(ValueError, match="max_tags"):
        TagPolicy(max_tags=1).validate(["one", "two"])


def test_gold_set_metrics_are_reportable() -> None:
    metrics = score_predictions(
        [{"note_id": "n", "project": "p", "acceptable_tags": ["a", "b"]}],
        {"n": {"project": "p", "tags": ["a", "new"]}},
    )
    assert metrics == {
        "tag_precision": 0.5,
        "tag_recall": 0.5,
        "new_tag_rate": 0.5,
        "project_accuracy": 1.0,
    }


def test_note_index_incremental_sync_and_related_links(tmp_path: Path) -> None:
    vault = Path("/Users/austinfrancis/Documents/quark_agent/mock_vault")
    with StateDatabase(tmp_path / "state.db") as state:
        index = NoteIndex(state)
        assert index.sync(vault) == 13
        assert index.sync(vault) == 0
        candidates = index.candidates("Inbox/Meeting Note.md", limit=5)
        assert candidates
        ids = [str(row["note_id"]) for row in candidates]
        assert "Inbox/Meeting Note.md" not in ids
        assert validate_related_ids(
            ids[:1], ids + ["Projects/Sanctions Research.md"], "Inbox/Meeting Note.md"
        )
        with pytest.raises(ValueError, match="unknown"):
            validate_related_ids(["missing.md"], ids, "Inbox/Meeting Note.md")
    assert "[[Projects/Sanctions Research]]" in render_related_section(
        ["Projects/Sanctions Research.md"]
    )
