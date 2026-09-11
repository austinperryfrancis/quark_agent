"""Run a small live routing evaluation against an installed Ollama model."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from time import monotonic

from quark.inference import Message, MessageRole, ModelProviderError, OllamaProvider, RootRouter
from quark.skills import SkillRegistry
from quark.skills.obsidian import build_obsidian_skills
from quark.skills.obsidian.models import ProposeTagsInput, TagProposal
from quark.skills.obsidian.prompts import build_tag_prompt


ROUTING_CASES = (
    ("Organize the latest note in my Obsidian inbox.", "obsidian.organize_note"),
    ("Add useful tags to Inbox/research.md.", "obsidian.organize_note"),
    ("What is the weather tomorrow?", None),
    ("Delete every file in my home directory.", None),
)

TAG_CASES = (
    ProposeTagsInput(
        path="Inbox/research.md",
        content=(
            "Research about semiconductor supply chains, export controls, and "
            "manufacturing capacity in Taiwan, Japan, and the United States."
        ),
        existing_tags=["semiconductors"],
    ),
    ProposeTagsInput(
        path="Projects/quark.md",
        content=(
            "Design notes for Quark's deterministic Skill runtime, local language "
            "models, persistence, and crash recovery."
        ),
        existing_tags=["quark"],
    ),
)


async def evaluate(model: str, vault: Path) -> dict:
    provider = OllamaProvider(model)
    registry = SkillRegistry()
    for skill in build_obsidian_skills(vault):
        registry.register(skill)
    router = RootRouter(registry, provider)
    results = []
    try:
        for request, expected in ROUTING_CASES:
            started = monotonic()
            try:
                decision = await router.route(request)
                actual = None if decision.unsupported else decision.skill
                result = {
                    "kind": "routing",
                    "request": request,
                    "expected": expected,
                    "actual": actual,
                    "passed": actual == expected,
                }
            except ModelProviderError as exc:
                result = {
                    "kind": "routing",
                    "request": request,
                    "expected": expected,
                    "actual": "ERROR",
                    "passed": False,
                    "error_code": exc.code,
                    "error": str(exc),
                }
            result["seconds"] = round(monotonic() - started, 3)
            results.append(result)
        for tag_input in TAG_CASES:
            started = monotonic()
            expected_existing = tag_input.existing_tags[0]
            try:
                proposal = await provider.generate_structured(
                    (
                        Message(
                            role=MessageRole.USER,
                            content=build_tag_prompt(tag_input),
                        ),
                    ),
                    TagProposal,
                )
                result = {
                    "kind": "tag_quality",
                    "note": tag_input.path,
                    "expected_existing": expected_existing,
                    "actual": proposal.tags,
                    "passed": expected_existing in proposal.tags,
                }
            except ModelProviderError as exc:
                result = {
                    "kind": "tag_quality",
                    "note": tag_input.path,
                    "expected_existing": expected_existing,
                    "actual": "ERROR",
                    "passed": False,
                    "error_code": exc.code,
                    "error": str(exc),
                }
            result["seconds"] = round(monotonic() - started, 3)
            results.append(result)
    finally:
        await provider.aclose()
    return {
        "model": model,
        "passed": sum(result["passed"] for result in results),
        "total": len(results),
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--vault", type=Path, default=Path.cwd())
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.model, args.vault)), indent=2))


if __name__ == "__main__":
    main()
