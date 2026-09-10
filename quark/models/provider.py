"""Provider-neutral local model interface."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol


class ModelProviderError(RuntimeError):
    """The provider could not complete a generation or health check."""


@dataclass(frozen=True, slots=True)
class ModelResponse:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    raw: dict[str, Any] | None = None


class ModelProvider(Protocol):
    provider_name: str
    model_name: str

    def generate(
        self,
        prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ModelResponse: ...

    def health_check(self, *, timeout_seconds: float | None = None) -> bool: ...

    def generate_stream(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Iterator[str]: ...
