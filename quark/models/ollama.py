"""Ollama HTTP model provider."""

from __future__ import annotations

import json
from collections.abc import Iterator
from urllib.error import URLError
from urllib.request import Request, urlopen

from quark.models.provider import ModelProviderError, ModelResponse


class OllamaProvider:
    provider_name = "ollama"

    def __init__(
        self,
        model_name: str,
        endpoint: str = "http://127.0.0.1:11434",
        *,
        think: bool = False,
    ) -> None:
        self.model_name = model_name
        self.endpoint = endpoint.rstrip("/")
        self.think = think

    def generate(
        self,
        prompt: str,
        *,
        schema: dict[str, object] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> ModelResponse:
        options = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False,
            "think": self.think,
            "options": options,
        }
        if schema is not None:
            payload["format"] = schema
        request = Request(
            f"{self.endpoint}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, UnicodeError, json.JSONDecodeError) as error:
            raise ModelProviderError(f"Ollama generation failed: {error}") from error
        if not isinstance(raw, dict) or not isinstance(raw.get("response"), str):
            raise ModelProviderError("Ollama response did not contain text")
        return ModelResponse(
            text=raw["response"],
            input_tokens=raw.get("prompt_eval_count"),
            output_tokens=raw.get("eval_count"),
            raw=raw,
        )

    def health_check(self, *, timeout_seconds: float | None = None) -> bool:
        try:
            with urlopen(
                f"{self.endpoint}/api/tags", timeout=timeout_seconds
            ) as response:
                return 200 <= int(response.status) < 300
        except (OSError, URLError):
            return False

    def generate_stream(
        self,
        prompt: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> Iterator[str]:
        options: dict[str, object] = {}
        if temperature is not None:
            options["temperature"] = temperature
        if max_tokens is not None:
            options["num_predict"] = max_tokens
        request = Request(
            f"{self.endpoint}/api/generate",
            data=json.dumps(
                {
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": True,
                    "think": self.think,
                    "options": options,
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                for line in response:
                    payload = json.loads(line.decode("utf-8"))
                    chunk = payload.get("response")
                    if isinstance(chunk, str) and chunk:
                        yield chunk
        except (OSError, URLError, UnicodeError, json.JSONDecodeError) as error:
            raise ModelProviderError(f"Ollama streaming failed: {error}") from error
