"""Quark command-line interface."""

import json
from pathlib import Path
from typing import Annotated

import typer

from quark.config import ConfigurationError, load_config
from quark.logging import configure_logging
from quark.state.database import StateDatabase

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
    configure_logging(
        settings.runtime.log_level,
        settings.runtime.log_format,
        tuple(settings.runtime.redact),
    )
    typer.echo(
        "Configuration valid "
        f"(provider={settings.model.provider}, dry_run={settings.runtime.dry_run})"
    )


@app.command("inspect-goal")
def inspect_goal(
    goal_id: int,
    database: Annotated[Path, typer.Option("--database", "-d")] = Path("quark.db"),
) -> None:
    """Print persisted goal, step, and event state."""
    with StateDatabase(database) as state:
        snapshot = state.inspect_goal(goal_id)
    if snapshot is None:
        typer.echo(f"Goal not found: {goal_id}", err=True)
        raise typer.Exit(code=1)
    typer.echo(json.dumps(snapshot, indent=2, default=str))


@app.command("why-changed")
def why_changed(
    note_path: str,
    database: Annotated[Path, typer.Option("--database", "-d")] = Path("quark.db"),
) -> None:
    """Show audit events whose payload references a note path."""
    with StateDatabase(database) as state:
        events = state.explain_change(note_path)
    typer.echo(json.dumps(events, indent=2, default=str))


__all__ = ["app"]
