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
