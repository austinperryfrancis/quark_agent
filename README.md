# Quark Agent

Quark Agent is a lightweight, local-first agent framework designed for small
language models on resource-constrained hardware. The initial skill organizes
and enriches Obsidian notes while the core runtime remains domain-independent.

The repository is currently scaffolded from the project architecture document.

## Development

Quark supports Python 3.11 and newer. Install the development environment with
`python3.11 -m venv .venv`, activate it, then run
`python -m pip install -e '.[dev]'`.

The baseline checks are `ruff check .`, `ruff format --check .`, `mypy`, and
`pytest`; CI runs the same commands.

The `skills` namespace contains installable domain modules. Skills are discovered
from their manifests by the capability loader, while Python operations use normal
`skills.<name>.operations` import paths. The core remains independent of skill
implementations.

Shared defaults live in `config/quark.yaml`. Machine-specific values such as the
vault path and local model name belong in ignored `config/quark.local.yaml`,
which is merged over the defaults. Run `quark check-config` to validate them.

## Interactive agent

Run `quark` with no subcommand to start a persistent conversational session.
Quark deterministically shortlists high-level intents, asks the small model only
when routing is ambiguous, and then either chats or invokes one executable skill.
Write-capable skill processes produce a preview and wait for a later `yes` before
applying changes. `/skills`, `/status`, `/clear`, `/help`, and `/exit` are handled
without model reasoning.

Set `vault.path` in the ignored `config/quark.local.yaml` to enable the Obsidian
skill. Explicit commands such as `quark organize` remain available for scripts.

Validated writes can retain original notes under `.quark/backups/` inside the
vault. `vault.backup_retention` controls how many timestamped backups are kept
per note; the default is five. The `.quark` directory is excluded from note
discovery. Backups are ordinary files and can be copied back to recover a note.
