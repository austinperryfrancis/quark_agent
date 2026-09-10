"""Application composition root for built-in providers and installed skills."""

from __future__ import annotations

from pathlib import Path

from quark.agent.agent import Agent
from quark.agent.skills import SkillContext, SkillRegistry
from quark.capabilities.loader import load_executable_skills
from quark.config import QuarkConfig
from quark.models.ollama import OllamaProvider
from quark.state.database import StateDatabase


def build_agent(
    config: QuarkConfig, state: StateDatabase, vault: Path | None = None
) -> Agent:
    provider = OllamaProvider(
        config.model.name, config.model.endpoint, think=config.model.think
    )
    selected_vault = vault or config.vault.path
    registry = (
        load_executable_skills(
            Path("skills"),
            SkillContext(
                config=config, state=state, vault=selected_vault, provider=provider
            ),
        )
        if selected_vault is not None
        else SkillRegistry()
    )
    return Agent(provider, state, registry)
