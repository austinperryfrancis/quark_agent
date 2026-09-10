"""Load and validate skill manifests from the skills directory."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from quark.agent.skills import ExecutableSkill, SkillContext, SkillRegistry
from quark.capabilities.registry import CapabilityError, CapabilityRegistry
from quark.capabilities.schemas import CapabilityMetadata, SkillManifest


def load_manifest(path: Path) -> tuple[SkillManifest, list[CapabilityMetadata]]:
    try:
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("skill"), dict):
            raise CapabilityError(f"manifest must contain a skill mapping: {path}")
        skill_data = dict(raw["skill"])
        for key in ("capabilities", "capability_metadata", "permissions", "intents"):
            if key in raw:
                skill_data.setdefault(key, raw[key])
        manifest = SkillManifest.model_validate(skill_data)
    except (
        OSError,
        UnicodeError,
        yaml.YAMLError,
        ValidationError,
        CapabilityError,
    ) as error:
        if isinstance(error, CapabilityError):
            raise
        raise CapabilityError(f"could not load manifest {path}: {error}") from error
    capabilities = []
    for name in manifest.capabilities:
        metadata = {"name": name, "domain": manifest.name}
        metadata.update(manifest.capability_metadata.get(name, {}))
        try:
            capabilities.append(CapabilityMetadata.model_validate(metadata))
        except Exception as error:
            raise CapabilityError(
                f"invalid capability {name} in {path}: {error}"
            ) from error
    return manifest, capabilities


def load_skills(root: Path) -> CapabilityRegistry:
    registry = CapabilityRegistry()
    manifests = sorted(root.glob("*/manifest.yaml"))
    if not manifests:
        raise CapabilityError(f"no skill manifests found in {root}")
    for path in manifests:
        _, capabilities = load_manifest(path)
        registry.register_many(capabilities)
    return registry


def load_executable_skills(root: Path, context: SkillContext) -> SkillRegistry:
    """Instantiate executable skills from validated manifest entry points."""
    registry = SkillRegistry()
    for path in sorted(root.glob("*/manifest.yaml")):
        manifest, _ = load_manifest(path)
        if manifest.entrypoint is None:
            continue
        module_name, separator, object_name = manifest.entrypoint.partition(":")
        if not separator:
            raise CapabilityError(f"invalid skill entrypoint: {manifest.entrypoint}")
        try:
            factory = getattr(import_module(module_name), object_name)
            skill: ExecutableSkill = factory(context)
        except (ImportError, AttributeError, TypeError, ValueError) as error:
            raise CapabilityError(
                f"could not load skill entrypoint {manifest.entrypoint}: {error}"
            ) from error
        registry.register(skill)
    return registry
