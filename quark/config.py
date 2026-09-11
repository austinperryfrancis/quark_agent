"""Validated file-based configuration for the Quark runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
import yaml

from quark.models import ReviewPolicy


class RuntimeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    socket_path: Path = Path(".quark/quark.sock")
    database_path: Path = Path(".quark/quark.db")
    max_depth: int = Field(default=8, ge=0)
    max_calls_per_run: int = Field(default=100, ge=1)
    max_validation_revisions: int = Field(default=2, ge=0)


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default: str = Field(default="qwen3:1.7b", min_length=1)
    ollama_url: str = Field(default="http://localhost:11434", min_length=1)


class ObsidianSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vault_path: Path | None = None
    inbox_path: str = Field(default="Inbox", min_length=1)


class TelegramSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    token: str | None = None

    @field_validator("token")
    @classmethod
    def reject_blank_token(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("telegram token cannot be blank")
        return value


class LoggingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"

    @field_validator("level")
    @classmethod
    def normalize_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("logging level must be DEBUG, INFO, WARNING, ERROR, or CRITICAL")
        return normalized


class SkillSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    review: ReviewPolicy | None = None

    @field_validator("review", mode="before")
    @classmethod
    def normalize_review(cls, value: Any) -> Any:
        return value.upper() if isinstance(value, str) else value


class QuarkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime: RuntimeSettings = Field(default_factory=RuntimeSettings)
    models: ModelSettings = Field(default_factory=ModelSettings)
    obsidian: ObsidianSettings = Field(default_factory=ObsidianSettings)
    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)
    skills: dict[str, SkillSettings] = Field(default_factory=dict)


def load_config(path: str | Path | None = None) -> QuarkConfig:
    if path is None:
        return QuarkConfig()
    config_path = Path(path)
    try:
        loaded = yaml.safe_load(config_path.read_text())
    except yaml.YAMLError as exc:
        raise ValueError(f"Invalid YAML in configuration {config_path}") from exc
    if loaded is None:
        loaded = {}
    if not isinstance(loaded, dict):
        raise ValueError("Quark configuration must be a YAML mapping")
    return QuarkConfig.model_validate(loaded)
