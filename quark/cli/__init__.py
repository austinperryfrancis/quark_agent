"""Quark command-line interface."""

from pathlib import Path
from typing import Annotated

import typer

from quark.config import ConfigurationError, load_config
from quark.logging import configure_logging

app = typer.Typer(
    name="quark",
    help="Harness-first local agent for small language models.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Run Quark Agent commands."""


@app.command("check-config")
def check_config(
    config: Annotated[Path, typer.Option("--config", "-c")] = Path("config/quark.yaml"),
    local_config: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Validate the effective configuration."""
    try:
        settings = load_config(config, local_config)
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    configure_logging(settings.runtime.log_level, settings.runtime.log_format)
    typer.echo(
        "Configuration valid "
        f"(provider={settings.model.provider}, dry_run={settings.runtime.dry_run})"
    )


__all__ = ["app"]
