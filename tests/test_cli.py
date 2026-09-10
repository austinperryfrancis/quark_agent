from typer.testing import CliRunner

from quark.cli import app


def test_help_runs() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "Harness-first local agent" in result.stdout


def test_check_config_reports_validation_error(tmp_path) -> None:
    invalid = tmp_path / "invalid.yaml"
    invalid.write_text("model:\n  temperature: 99\n")
    result = CliRunner().invoke(app, ["check-config", "--config", str(invalid)])
    assert result.exit_code == 2
    assert "model.temperature" in result.output


def test_bare_quark_starts_repl_and_lists_configured_skill(tmp_path) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    config = tmp_path / "test.yaml"
    config.write_text(
        f"runtime:\n  database: {tmp_path / 'chat.db'}\nvault:\n  path: {vault}\n"
    )
    result = CliRunner().invoke(
        app, ["--config", str(config)], input="/skills\n/exit\n"
    )
    assert result.exit_code == 0
    assert "Available skills: obsidian" in result.stdout
    assert "Goodbye" in result.stdout
