from pathlib import Path

import pytest

from quark.config import ConfigurationError, load_config


def test_load_config_merges_local_override(tmp_path: Path) -> None:
    shared = tmp_path / "quark.yaml"
    shared.write_text("runtime:\n  dry_run: true\nmodel:\n  name: shared\n")
    local = tmp_path / "quark.local.yaml"
    local.write_text("model:\n  name: local\nvault:\n  path: /tmp/example-vault\n")

    config = load_config(shared)

    assert config.runtime.dry_run is True
    assert config.model.name == "local"
    assert config.vault.path == Path("/tmp/example-vault")


def test_load_config_reports_field_path(tmp_path: Path) -> None:
    config_file = tmp_path / "quark.yaml"
    config_file.write_text("retries:\n  max_attempts: 0\n")

    with pytest.raises(ConfigurationError, match="retries.max_attempts"):
        load_config(config_file)


def test_load_config_rejects_unknown_settings(tmp_path: Path) -> None:
    config_file = tmp_path / "quark.yaml"
    config_file.write_text("runtime:\n  dry_rnu: true\n")

    with pytest.raises(ConfigurationError, match="runtime.dry_rnu"):
        load_config(config_file)
