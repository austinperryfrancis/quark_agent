"""Quark command-line interface."""

import json
from pathlib import Path
from typing import Annotated

import typer

from quark.config import ConfigurationError, QuarkConfig, load_config
from quark.logging import configure_logging
from quark.state.database import StateDatabase
from skills.obsidian.operations.apply_changes import apply_changes
from skills.obsidian.operations.generate_frontmatter import (
    FrontmatterConfig as NoteFrontmatterConfig,
)
from skills.obsidian.operations.generate_frontmatter import (
    generate_frontmatter,
)
from skills.obsidian.operations.list_notes import list_notes
from skills.obsidian.operations.parse_frontmatter import parse_note
from skills.obsidian.operations.read_note import read_note

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


def _organize(
    vault: Path,
    settings: QuarkConfig,
    *,
    apply: bool,
    max_notes: int | None,
    changed_only: bool,
    database: Path,
) -> int:
    config = settings
    notes = list_notes(vault, config.vault.exclude)
    if max_notes is not None:
        notes = notes[:max_notes]
    completed = unchanged = failed = 0
    with StateDatabase(database) as state:
        for reference in notes:
            try:
                parsed = parse_note(
                    read_note(vault, reference), vault, config.vault.exclude
                )
                patch = generate_frontmatter(
                    parsed,
                    deterministic_fields={
                        "updated": parsed.document.modified_at.date().isoformat()
                    },
                    config=NoteFrontmatterConfig(
                        field_order=tuple(config.frontmatter.field_order),
                        preserve_unknown_fields=config.frontmatter.preserve_unknown_fields,
                        max_tags=config.frontmatter.max_tags,
                    ),
                )
                if not patch.diff:
                    unchanged += 1
                    typer.echo(f"UNCHANGED {reference.note_id}")
                    continue
                typer.echo(
                    f"  project={patch.frontmatter.get('project', 'none')} "
                    f"tags={patch.frontmatter.get('tags', [])} "
                    f"changes={list(patch.changed_fields)}"
                )
                result = apply_changes(
                    vault,
                    patch,
                    dry_run=not apply,
                    create_backup=config.vault.create_backups,
                    backup_retention=config.vault.backup_retention,
                )
                state.record_event(
                    "note_organized",
                    payload={
                        "note_id": reference.note_id,
                        "dry_run": result.dry_run,
                        "diff": patch.diff,
                    },
                )
                completed += 1
                typer.echo(f"{'APPLIED' if apply else 'PREVIEW'} {reference.note_id}")
                if not apply:
                    typer.echo(patch.diff, nl=False)
            except Exception as error:  # continue after isolated note failures
                failed += 1
                typer.echo(f"FAILED {reference.note_id}: {error}", err=True)
    typer.echo(f"Summary: completed={completed} unchanged={unchanged} failed={failed}")
    return 3 if failed else 0


@app.command("organize")
def organize(
    vault: Path,
    apply: bool = typer.Option(False, "--apply", help="Actually write changes."),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    max_notes: int | None = typer.Option(None, "--max-notes", min=1),
    changed_only: bool = typer.Option(False, "--changed-only"),
    model: str | None = typer.Option(None, "--model"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
    config: Path = typer.Option(Path("config/quark.yaml"), "--config", "-c"),  # noqa: B008
    database: Path | None = typer.Option(None, "--database", "-d"),  # noqa: B008
) -> None:  # noqa: B008
    """Preview or apply deterministic frontmatter organization for a vault."""
    try:
        settings = load_config(config)
        db = database or settings.runtime.database
        code = _organize(
            vault,
            settings,
            apply=apply or not dry_run,
            max_notes=max_notes,
            changed_only=changed_only,
            database=db,
        )
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    if code:
        raise typer.Exit(code=code)


@app.command("organize-inbox")
def organize_inbox(
    vault: Path,
    apply: bool = typer.Option(False, "--apply"),
    dry_run: bool = typer.Option(True, "--dry-run/--no-dry-run"),
    max_notes: int | None = typer.Option(None, "--max-notes", min=1),
    model: str | None = typer.Option(None, "--model"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
    non_interactive: bool = typer.Option(False, "--non-interactive"),
    config: Path = typer.Option(Path("config/quark.yaml"), "--config", "-c"),  # noqa: B008
    database: Path | None = typer.Option(None, "--database", "-d"),  # noqa: B008
) -> None:  # noqa: B008
    """Preview or apply organization for notes in the configured inbox."""
    try:
        settings = load_config(config)
        inbox = vault / settings.vault.inbox
        code = _organize(
            inbox,
            settings,
            apply=apply or not dry_run,
            max_notes=max_notes,
            changed_only=False,
            database=database or settings.runtime.database,
        )
    except ConfigurationError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    if code:
        raise typer.Exit(code=code)


@app.command("retry-goal")
def retry_goal(
    goal_id: int,
    database: Annotated[Path, typer.Option("--database", "-d")] = Path("quark.db"),
) -> None:
    """Move failed or blocked steps for a goal back to pending."""
    with StateDatabase(database) as state:
        with state.transaction() as connection:
            cursor = connection.execute(
                "UPDATE steps SET status='pending', completed_at=NULL WHERE goal_id=? AND status IN ('failed','blocked')",
                (goal_id,),
            )
            count = cursor.rowcount
        state.record_event(
            "goal_retry_requested", goal_id=goal_id, payload={"steps_reset": count}
        )
    typer.echo(f"Goal {goal_id}: reset {count} failed/blocked steps")


__all__ = ["app"]
