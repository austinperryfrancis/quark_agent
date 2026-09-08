"""Runtime capability registry with duplicate detection."""

from __future__ import annotations

from collections.abc import Iterable

from quark.capabilities.schemas import CapabilityMetadata


class CapabilityError(ValueError):
    """Capability metadata or registration is invalid."""


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilityMetadata] = {}

    def register(self, metadata: CapabilityMetadata) -> None:
        if metadata.name in self._capabilities:
            raise CapabilityError(f"duplicate capability: {metadata.name}")
        self._capabilities[metadata.name] = metadata

    def register_many(self, capabilities: Iterable[CapabilityMetadata]) -> None:
        for capability in capabilities:
            self.register(capability)

    def get(self, name: str) -> CapabilityMetadata:
        try:
            return self._capabilities[name]
        except KeyError as error:
            raise CapabilityError(f"unknown capability: {name}") from error

    def maybe_get(self, name: str) -> CapabilityMetadata | None:
        return self._capabilities.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._capabilities))

    def __len__(self) -> int:
        return len(self._capabilities)
