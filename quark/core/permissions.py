"""Capability risk classes and permission policies."""

from __future__ import annotations

from dataclasses import dataclass

from quark.capabilities.schemas import CapabilityMetadata, RiskClass
from quark.config import PermissionsConfig


@dataclass(frozen=True, slots=True)
class PermissionDecision:
    allowed: bool
    requires_approval: bool
    reason: str


def authorize(
    capability: CapabilityMetadata,
    policy: PermissionsConfig,
    *,
    approved: bool = False,
) -> PermissionDecision:
    """Apply deterministic risk policy before a capability can execute."""
    if capability.risk is RiskClass.READ and not policy.allow_read:
        return PermissionDecision(False, False, "read operations are disabled")
    if capability.risk is RiskClass.WRITE and not policy.allow_write and not approved:
        return PermissionDecision(False, True, "write permission is disabled")
    if (
        capability.risk is RiskClass.DESTRUCTIVE
        and policy.require_destructive_approval
        and not approved
    ):
        return PermissionDecision(False, True, "destructive approval required")
    if (
        capability.risk is RiskClass.EXTERNAL
        and policy.require_external_approval
        and not approved
    ):
        return PermissionDecision(False, True, "external approval required")
    if capability.risk is RiskClass.PRIVILEGED and not approved:
        return PermissionDecision(False, True, "privileged approval required")
    return PermissionDecision(True, False, "allowed by policy")
