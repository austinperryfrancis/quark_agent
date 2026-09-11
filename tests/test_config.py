import asyncio
from pathlib import Path

from pydantic import ValidationError
import pytest

from quark.config import QuarkConfig, load_config
from quark.main import _build_runtime
from quark.models import ReviewPolicy


def test_default_configuration_is_valid_and_local() -> None:
    config = load_config()

    assert config.runtime.database_path == Path.home() / ".quark/quark.db"
    assert config.runtime.max_depth == 8
    assert config.models.default == "qwen3:1.7b"
    assert not config.telegram.enabled


def test_yaml_configuration_loads_typed_values_and_normalizes_enums(tmp_path) -> None:
    path = tmp_path / "quark.yaml"
    path.write_text(
        """
runtime:
  max_depth: 4
  max_calls_per_run: 25
logging:
  level: debug
skills:
  obsidian.tags.apply:
    review: never
""".strip()
    )

    config = load_config(path)

    assert config.runtime.max_depth == 4
    assert config.runtime.max_calls_per_run == 25
    assert config.logging.level == "DEBUG"
    assert config.skills["obsidian.tags.apply"].review is ReviewPolicy.NEVER


def test_configuration_rejects_unknown_fields_and_invalid_limits(tmp_path) -> None:
    path = tmp_path / "bad.yaml"
    path.write_text("runtime:\n  max_depth: -1\n  mystery: true\n")

    with pytest.raises(ValidationError):
        load_config(path)


def test_runtime_applies_review_overrides_and_execution_limits(tmp_path) -> None:
    config = QuarkConfig.model_validate(
        {
            "runtime": {
                "max_depth": 3,
                "max_calls_per_run": 12,
                "max_validation_revisions": 1,
            },
            "skills": {"obsidian.tags.apply": {"review": "never"}},
        }
    )
    vault = tmp_path / "Vault"
    vault.mkdir()
    runtime, store, provider = _build_runtime(
        tmp_path / "quark.db",
        "test-model",
        "http://localhost:11434",
        vault,
        config=config,
    )

    assert runtime.registry.get("obsidian.tags.apply").review_policy is ReviewPolicy.NEVER
    assert runtime.runner.max_depth == 3
    assert runtime.runner.max_total_calls == 12
    assert runtime.runner.max_validation_revisions == 1

    store.close()
    asyncio.run(provider.aclose())


def test_disabling_child_removes_dependent_composite(tmp_path) -> None:
    config = QuarkConfig.model_validate(
        {"skills": {"obsidian.tags.apply": {"enabled": False}}}
    )
    vault = tmp_path / "Vault"
    vault.mkdir()
    runtime, store, provider = _build_runtime(
        tmp_path / "quark.db",
        "test-model",
        "http://localhost:11434",
        vault,
        config=config,
    )

    assert "obsidian.tags.apply" not in runtime.registry
    assert "obsidian.organize_note" not in runtime.registry
    assert "obsidian.note.read" in runtime.registry

    store.close()
    asyncio.run(provider.aclose())
