"""Deterministic Quark service used by the subprocess restart test."""

from __future__ import annotations

import argparse
import asyncio
from collections import deque
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from quark.inference import (
    ArgumentDecision,
    Message,
    ModelProvider,
    RootRouter,
    RouteDecision,
    ValidationDecision,
    ValidationOutcome,
)
from quark.models import SideEffect
from quark.persistence import SQLiteStateStore
from quark.runtime import QuarkRuntime, RuntimeService, SkillRunner
from quark.skills import Skill, SkillContext, SkillRegistry


class TextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


class TextOutput(BaseModel):
    text: str


class MarkerWriteSkill(Skill):
    name = "test.marker.write"
    description = "Write text to the configured test marker."
    input_schema = TextInput
    output_schema = TextOutput
    side_effect = SideEffect.LOCAL_WRITE
    top_level = True

    def __init__(self, marker: Path) -> None:
        self.marker = marker

    async def run(self, ctx: SkillContext, args: BaseModel) -> TextOutput:
        assert isinstance(args, TextInput)
        self.marker.write_text(args.text)
        return TextOutput(text=args.text)


class FixedProvider(ModelProvider):
    def __init__(self) -> None:
        self.outputs = deque(
            (
                RouteDecision(skill="test.marker.write"),
                ArgumentDecision(arguments={"text": "persisted"}),
                ValidationDecision(decision=ValidationOutcome.APPROVE),
            )
        )

    async def generate_structured(
        self,
        messages: tuple[Message, ...],
        schema: type[BaseModel],
        options: dict[str, Any] | None = None,
    ) -> BaseModel:
        result = self.outputs.popleft()
        assert isinstance(result, schema)
        return result


async def serve(database: Path, socket: Path, marker: Path) -> None:
    store = SQLiteStateStore(database)
    provider = FixedProvider()
    registry = SkillRegistry()
    registry.register(MarkerWriteSkill(marker))
    router = RootRouter(registry, provider)
    runner = SkillRunner(registry, provider=provider, state_store=store)
    service = RuntimeService(QuarkRuntime(registry, router, runner), socket)
    try:
        await service.serve_forever()
    finally:
        await service.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("database", type=Path)
    parser.add_argument("socket", type=Path)
    parser.add_argument("marker", type=Path)
    arguments = parser.parse_args()
    asyncio.run(serve(arguments.database, arguments.socket, arguments.marker))
