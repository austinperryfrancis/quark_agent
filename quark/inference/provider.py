"""Provider-neutral structured generation interface."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

from quark.inference.models import Message

StructuredOutput = TypeVar("StructuredOutput", bound=BaseModel)


class ModelProviderError(RuntimeError):
    """A controlled model transport or structured-output failure."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        recoverable: bool,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.recoverable = recoverable
        self.details = details
        super().__init__(message)


class ModelProvider(ABC):
    @abstractmethod
    async def generate_structured(
        self,
        messages: tuple[Message, ...],
        schema: type[StructuredOutput],
        options: dict[str, Any] | None = None,
    ) -> StructuredOutput:
        """Generate output that has been validated against `schema`."""
        raise NotImplementedError
