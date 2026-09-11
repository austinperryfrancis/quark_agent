"""Command-line gateways for the long-lived Quark runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import sys
from typing import Sequence

from quark.gateways import (
    GatewayCommand,
    GatewayRequest,
    GatewayResponse,
    LocalRuntimeClient,
    RecoveryAction,
    TelegramGateway,
)
from quark.config import QuarkConfig, load_config
from quark.inference import OllamaProvider, RootRouter
from quark.persistence import SQLiteStateStore
from quark.runtime import QuarkRuntime, RuntimeService, SkillRunner
from quark.skills import SkillRegistry
from quark.skills.obsidian import build_obsidian_skills

DEFAULT_RUNTIME_DIRECTORY = Path.home() / ".quark"

QUARK_BANNER = r"""
             .       *
         *       .
      -----(  q  )-----
         .       *
            Q U A R K
      deterministic skills
""".strip("\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quark")
    subcommands = parser.add_subparsers(dest="command", required=True)

    serve = subcommands.add_parser("serve", help="Run the Quark service")
    serve.add_argument("--config", type=Path)
    serve.add_argument("--socket", type=Path)
    serve.add_argument("--database", type=Path)
    serve.add_argument("--model")
    serve.add_argument("--ollama-url")
    serve.add_argument("--vault", type=Path)
    serve.add_argument("--inbox")
    serve.add_argument("--telegram-token")

    chat = subcommands.add_parser("chat", help="Chat through the Quark service")
    chat.add_argument("--socket", type=Path, default=DEFAULT_RUNTIME_DIRECTORY / "quark.sock")
    chat.add_argument("--session", default="cli:default")

    for command in ("status", "skills", "runs"):
        child = subcommands.add_parser(command, help=f"Show Quark {command}")
        child.add_argument("--socket", type=Path, default=DEFAULT_RUNTIME_DIRECTORY / "quark.sock")

    run = subcommands.add_parser("run", help="Inspect one persisted Run")
    run.add_argument("run_id")
    run.add_argument("--socket", type=Path, default=DEFAULT_RUNTIME_DIRECTORY / "quark.sock")

    recover = subcommands.add_parser("recover", help="Recover an interrupted Run")
    recover.add_argument("run_id")
    recover.add_argument("action", choices=("retry", "cancel"))
    recover.add_argument(
        "--call", dest="skill_call_id", help="Interrupted child SkillCall to retry"
    )
    recover.add_argument("--socket", type=Path, default=DEFAULT_RUNTIME_DIRECTORY / "quark.sock")
    return parser


def _build_runtime(
    database: Path,
    model: str,
    ollama_url: str,
    vault: Path | None = None,
    inbox: str = "Inbox",
    config: QuarkConfig | None = None,
) -> tuple[QuarkRuntime, SQLiteStateStore, OllamaProvider]:
    config = config or QuarkConfig()
    registry = SkillRegistry()
    installed = ()
    if vault is not None:
        installed = build_obsidian_skills(vault, inbox_path=inbox)
    installed_names = {skill.name for skill in installed}
    unknown_overrides = set(config.skills) - installed_names
    if unknown_overrides:
        names = ", ".join(sorted(unknown_overrides))
        raise ValueError(f"Configuration references unavailable Skills: {names}")
    enabled = {
        skill.name
        for skill in installed
        if config.skills.get(skill.name) is None
        or config.skills[skill.name].enabled
    }
    changed = True
    while changed:
        changed = False
        for skill in installed:
            if skill.name in enabled and any(
                child not in enabled for child in skill.allowed_children
            ):
                enabled.remove(skill.name)
                changed = True
    for skill in installed:
        if skill.name not in enabled:
            continue
        override = config.skills.get(skill.name)
        if override is not None and override.review is not None:
            skill.review_policy = override.review
        registry.register(skill)
    registry.validate_graph()
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SQLiteStateStore(database)
    provider = OllamaProvider(model, base_url=ollama_url)
    router = RootRouter(registry, provider)
    runner = SkillRunner(
        registry,
        provider=provider,
        state_store=store,
        max_depth=config.runtime.max_depth,
        max_total_calls=config.runtime.max_calls_per_run,
        max_validation_revisions=config.runtime.max_validation_revisions,
    )
    return QuarkRuntime(registry, router, runner), store, provider


async def _serve(args: argparse.Namespace) -> None:
    config = load_config(args.config)
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    database = args.database or config.runtime.database_path
    socket = args.socket or config.runtime.socket_path
    model = args.model or config.models.default
    ollama_url = args.ollama_url or config.models.ollama_url
    vault = args.vault or config.obsidian.vault_path
    inbox = args.inbox or config.obsidian.inbox_path
    environment_token = os.environ.get("QUARK_TELEGRAM_TOKEN")
    telegram_token = args.telegram_token or environment_token or config.telegram.token
    telegram_enabled = config.telegram.enabled or bool(
        args.telegram_token or environment_token
    )
    if telegram_enabled and (not telegram_token or not telegram_token.strip()):
        raise ValueError("Telegram is enabled but no token is configured")
    runtime, store, provider = _build_runtime(
        database, model, ollama_url, vault, inbox, config
    )
    service = RuntimeService(runtime, socket)
    telegram = TelegramGateway(runtime, telegram_token) if telegram_enabled else None
    logging.getLogger(__name__).info(
        "Quark starting database=%s skills=%d telegram=%s",
        database,
        len(runtime.registry),
        telegram_enabled,
    )
    try:
        if telegram is None:
            await service.serve_forever()
        else:
            await asyncio.gather(service.serve_forever(), telegram.run())
    finally:
        await service.close()
        if telegram is not None:
            await telegram.aclose()
        await provider.aclose()
        store.close()
        logging.getLogger(__name__).info("Quark shutdown complete")


async def _request(socket: Path, request: GatewayRequest) -> GatewayResponse:
    return await LocalRuntimeClient(socket).request(request)


async def _request_with_activity(
    socket: Path, request: GatewayRequest, *, executing: bool = False
) -> GatewayResponse:
    if not sys.stdout.isatty():
        return await _request(socket, request)
    frames = ("|", "/", "-", "\\")
    words = (
        ("executing", "working", "finishing")
        if executing
        else ("working", "thinking", "mulling", "checking")
    )
    task = asyncio.create_task(_request(socket, request))
    step = 0
    try:
        while not task.done():
            word = words[(step // 10) % len(words)]
            print(
                f"\r{frames[step % len(frames)]} Quark is {word}...",
                end="",
                flush=True,
            )
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=0.12)
            except TimeoutError:
                step += 1
        return await task
    finally:
        print("\r\033[2K", end="", flush=True)


def _print_response(response: GatewayResponse) -> None:
    print(response.message)
    if response.data:
        print(json.dumps(response.data, indent=2, sort_keys=True))


def _gateway_request(args: argparse.Namespace) -> GatewayRequest:
    command = GatewayCommand(args.command.upper())
    if command is GatewayCommand.RUN:
        return GatewayRequest(command=command, run_id=args.run_id)
    if command is GatewayCommand.RECOVER:
        return GatewayRequest(
            command=command,
            run_id=args.run_id,
            skill_call_id=args.skill_call_id,
            recovery_action=RecoveryAction(args.action.upper()),
        )
    return GatewayRequest(command=command)


def _chat(args: argparse.Namespace) -> None:
    print(QUARK_BANNER)
    status = asyncio.run(
        _request(args.socket, GatewayRequest(command=GatewayCommand.STATUS))
    )
    skill_count = status.data.get("installed_skills", 0)
    waiting_count = status.data.get("waiting_runs", 0)
    print(f"\nRuntime online | {skill_count} Skills | {waiting_count} waiting Runs")
    if skill_count == 0:
        print(
            "No Skills are enabled. Restart `quark serve` with --vault PATH "
            "or a configured vault_path."
        )
    print("Type a request. Ctrl-D to exit.\n")
    while True:
        try:
            text = input("You > ").strip()
        except EOFError:
            print()
            return
        if not text:
            continue
        response = asyncio.run(
            _request_with_activity(
                args.socket,
                GatewayRequest(
                    command=GatewayCommand.CHAT,
                    session_key=args.session,
                    text=text,
                ),
                executing=text.casefold().strip().rstrip(".!?")
                in {"yes", "y", "approve", "approved", "ok", "okay"},
            )
        )
        print(f"Quark > {response.message}")
        if response.data:
            print(json.dumps(response.data, indent=2, sort_keys=True))


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "serve":
            asyncio.run(_serve(args))
        elif args.command == "chat":
            _chat(args)
        else:
            response = asyncio.run(
                _request(
                    args.socket,
                    _gateway_request(args),
                )
            )
            _print_response(response)
            return 0 if response.ok else 1
    except KeyboardInterrupt:
        return 130
    except (ConnectionError, OSError) as exc:
        print(f"Unable to connect to Quark runtime: {exc}")
        return 1
    except ValueError as exc:
        print(f"Invalid Quark configuration: {exc}")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
