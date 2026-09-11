"""Ollama implementation of Quark's structured model provider."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from quark.inference.models import Message, MessageRole
from quark.inference.provider import ModelProvider, ModelProviderError, StructuredOutput


class _OllamaMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: str


class _OllamaResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: _OllamaMessage
    done: bool


class OllamaProvider(ModelProvider):
    """Call Ollama's local non-streaming chat API with a JSON schema."""

    def __init__(
        self,
        model: str,
        *,
        base_url: str = "http://localhost:11434",
        timeout_seconds: float = 120.0,
        max_parse_repairs: int = 1,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model:
            raise ValueError("model cannot be empty")
        if max_parse_repairs not in (0, 1):
            raise ValueError("max_parse_repairs must be 0 or 1")
        self.model = model
        self.max_parse_repairs = max_parse_repairs
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds
        )

    async def generate_structured(
        self,
        messages: tuple[Message, ...],
        schema: type[StructuredOutput],
        options: dict[str, Any] | None = None,
    ) -> StructuredOutput:
        if not messages:
            raise ValueError("messages cannot be empty")

        current_messages = list(messages)
        for attempt in range(self.max_parse_repairs + 1):
            content = await self._chat(current_messages, schema, options)
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                if attempt == self.max_parse_repairs:
                    raise ModelProviderError(
                        "INVALID_STRUCTURED_OUTPUT",
                        "Ollama returned output that did not match the requested schema.",
                        recoverable=False,
                        details={"errors": exc.errors(include_url=False)},
                    ) from exc
                current_messages = [
                    *current_messages,
                    Message(role=MessageRole.ASSISTANT, content=content),
                    Message(
                        role=MessageRole.USER,
                        content="Return corrected JSON matching the required schema.",
                    ),
                ]
        raise AssertionError("bounded repair loop exhausted unexpectedly")

    async def _chat(
        self,
        messages: list[Message],
        schema: type[BaseModel],
        options: dict[str, Any] | None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [message.model_dump(mode="json") for message in messages],
            "format": schema.model_json_schema(),
            "stream": False,
            "options": {"temperature": 0, **(options or {})},
        }
        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            envelope = _OllamaResponse.model_validate(response.json())
        except httpx.TimeoutException as exc:
            raise ModelProviderError(
                "MODEL_TIMEOUT", "Ollama inference timed out.", recoverable=True
            ) from exc
        except httpx.RequestError as exc:
            raise ModelProviderError(
                "MODEL_UNAVAILABLE",
                "Could not connect to Ollama.",
                recoverable=True,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise ModelProviderError(
                "MODEL_HTTP_ERROR",
                self._http_error_message(exc.response),
                recoverable=exc.response.status_code >= 500,
                details={"status_code": exc.response.status_code},
            ) from exc
        except (ValueError, ValidationError) as exc:
            raise ModelProviderError(
                "INVALID_PROVIDER_RESPONSE",
                "Ollama returned an invalid response envelope.",
                recoverable=True,
            ) from exc
        if not envelope.done:
            raise ModelProviderError(
                "INCOMPLETE_PROVIDER_RESPONSE",
                "Ollama returned an incomplete non-streaming response.",
                recoverable=True,
            )
        return envelope.message.content

    @staticmethod
    def _http_error_message(response: httpx.Response) -> str:
        try:
            error = response.json().get("error")
        except ValueError:
            error = None
        return str(error) if error else f"Ollama returned HTTP {response.status_code}."

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
