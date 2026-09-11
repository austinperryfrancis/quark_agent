# Quark Agent

Quark is an always-on, local-first agent runtime built around small, explicit,
typed Skills. Complex behavior belongs in tested Skill implementations; the
runtime remains deterministic and inspectable.

This repository currently contains the architecture foundation through Phase 12:

- Pydantic domain models for sessions, runs, SkillCalls, revisions, pending
  interactions, and structured errors
- one shared `Skill` base class for atomic and composite capabilities
- a duplicate-safe Skill registry
- a deterministic runner with schema validation and enforced child permissions
- a provider-neutral structured-inference contract and Ollama adapter
- a versioned, minimal-context root router that selects one top-level Skill
- typed argument generation and bounded semantic SkillCall validation
- conversational pending-call review with approval, edits, rejection, and cancellation
- SQLite runtime state, revision history, pending interactions, and event logging
- one long-lived runtime exposed through local CLI gateway commands
- a Telegram long-polling gateway connected to that same runtime
- a safe initial Obsidian Skill tree for inbox discovery, reading, tag proposal,
  reviewed tag application, and recursive note organization
- startup state hydration, restoration of waiting reviews, interrupted-call
  quarantine, and deterministic composite continuation using persisted children
- per-session execution serialization and explicit interrupted-run inspection,
  retry, and cancellation commands
- tests proving recursive Skill execution and the core call lifecycle

It intentionally does not yet include semantic memory, scheduling, shell execution,
web search, or dynamic Skill creation.

## Development

Use Python 3.11 or newer, install the development dependencies, and run:

```bash
python -m pytest
```

The architecture specification is authoritative for future work.

Run the opt-in live routing evaluation against an installed Ollama model with:

```bash
python -m scripts.evaluate_local_model --model qwen3:1.7b
```

This contacts the local Ollama service and reports per-case correctness, latency,
and structured-output failures. It is intentionally separate from the offline
unit suite.

## Runtime

Start the single local runtime in one terminal:

```bash
quark serve
```

The default socket and database live under `~/.quark`, so gateway commands work
from any current directory. `quark chat` checks the runtime immediately and
shows how many Skills are enabled before accepting requests.

For validated file-based configuration, copy `config.example.yaml`, edit it,
and start:

```bash
quark serve --config quark.yaml
```

Command-line values override their corresponding configuration values. Runtime
limits, model settings, Obsidian paths, Telegram enablement, log level, Skill
enablement, and per-Skill review policies are validated at startup. Normal logs
show lifecycle event names and Run/SkillCall identifiers; detailed state remains
in SQLite.

Connect from another terminal:

```bash
quark chat
quark status
quark skills
quark runs
quark run RUN_ID
quark recover RUN_ID retry
quark recover RUN_ID retry --call CALL_ID
quark recover RUN_ID cancel
```

`retry` is intentionally limited to one unambiguous, recoverable interrupted
atomic call. Composite recovery requires `--call`; after that child succeeds,
Quark resumes its parents while reusing completed child results.

To enable the initial Obsidian Skill tree:

```bash
quark serve --vault /path/to/Vault --inbox Inbox
```

Without `--vault`, the Skill registry remains empty and chat requests return
`UNSUPPORTED`.

Enable Telegram on the same runtime by setting `QUARK_TELEGRAM_TOKEN` before
starting `quark serve`. Telegram chats map to sessions named
`telegram:<chat_id>` and use the same routing, review, and execution state as
the CLI gateway.
