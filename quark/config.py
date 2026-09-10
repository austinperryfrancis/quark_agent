"""Typed configuration loading with user-local overrides."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RuntimeConfig(StrictModel):
    database: Path = Path("quark.db")
    dry_run: bool = True
    log_format: Literal["console", "json"] = "console"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    redact: list[str] = Field(default_factory=list)
    output_dir: Path = Path("output")


class ModelConfig(StrictModel):
    provider: str = "ollama"
    name: str = "qwen2.5:1.5b"
    endpoint: str = "http://127.0.0.1:11434"
    temperature: float = Field(default=0.1, ge=0.0, le=2.0)
    max_tokens: int = Field(default=256, gt=0)
    timeout_seconds: float = Field(default=60.0, gt=0)
    think: bool = False


class VaultConfig(StrictModel):
    path: Path | None = None
    inbox: Path = Path("Inbox")
    exclude: list[str] = Field(
        default_factory=lambda: [
            ".obsidian",
            ".quark",
            "Templates",
            "Archive",
            "backups",
        ]
    )
    create_backups: bool = True
    backup_retention: int = Field(default=5, ge=1)
    project_choices: list[str] = Field(default_factory=list)
    protected_tags: list[str] = Field(default_factory=list)
    forbidden_tags: list[str] = Field(default_factory=list)


class PermissionsConfig(StrictModel):
    allow_read: bool = True
    allow_write: bool = False
    require_destructive_approval: bool = True
    require_external_approval: bool = True


class FrontmatterConfig(StrictModel):
    field_order: list[str] = Field(
        default_factory=lambda: [
            "title",
            "type",
            "project",
            "created",
            "updated",
            "tags",
            "status",
            "action_required",
            "people",
        ]
    )
    preserve_unknown_fields: bool = True
    max_tags: int = Field(default=8, gt=0)

    @field_validator("field_order")
    @classmethod
    def unique_fields(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("field_order must not contain duplicate fields")
        return value


class RetryConfig(StrictModel):
    max_attempts: int = Field(default=3, ge=1, le=10)
    initial_delay_seconds: float = Field(default=0.25, ge=0)
    backoff_multiplier: float = Field(default=2.0, ge=1)


class WorkflowConfig(StrictModel):
    validation: Literal["user", "agent", "off"] = "user"


class QuarkConfig(StrictModel):
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    model: ModelConfig = Field(default_factory=ModelConfig)
    vault: VaultConfig = Field(default_factory=VaultConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    frontmatter: FrontmatterConfig = Field(default_factory=FrontmatterConfig)
    retries: RetryConfig = Field(default_factory=RetryConfig)
    workflows: WorkflowConfig = Field(default_factory=WorkflowConfig)


class ConfigurationError(ValueError):
    """A configuration file could not be read or validated."""


def _read_yaml(path: Path, *, required: bool) -> dict[str, Any]:
    if not path.exists():
        if required:
            raise ConfigurationError(f"Configuration file does not exist: {path}")
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as error:
        message = f"Could not read configuration {path}: {error}"
        raise ConfigurationError(message) from error
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ConfigurationError(f"Configuration root must be a mapping: {path}")
    return raw


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(
    path: Path = Path("config/quark.yaml"),
    local_path: Path | None = None,
) -> QuarkConfig:
    """Load shared defaults and merge an ignored local override when present."""
    override = local_path or path.with_name("quark.local.yaml")
    data = _merge(_read_yaml(path, required=True), _read_yaml(override, required=False))
    try:
        return QuarkConfig.model_validate(data)
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in issue['loc'])}: {issue['msg']}"
            for issue in error.errors()
        )
        raise ConfigurationError(f"Invalid configuration: {details}") from error
