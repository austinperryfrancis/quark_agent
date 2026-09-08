"""Validated semantic microtasks with retries and content-aware caching."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from quark.config import RetryConfig
from quark.core.context import SemanticTask, compile_context
from quark.models.prompts import load_prompt
from quark.models.provider import ModelProvider, ModelProviderError
from quark.state.database import StateDatabase


class SemanticError(RuntimeError):
    """A semantic microtask exhausted its deterministic recovery policy."""


@dataclass(frozen=True, slots=True)
class SemanticResult:
    value: Any
    attempts: int
    cached: bool
    prompt: str


def _schema_errors(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    type_ok = {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }
    if expected in type_ok and not type_ok[expected]:
        return [f"{path} must be {expected}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path} must be one of {schema['enum']}")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}.{key} is required")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            errors.extend(
                f"{path}.{key} is not allowed" for key in value if key not in properties
            )
        for key, child_schema in properties.items():
            if key in value and isinstance(child_schema, dict):
                errors.extend(_schema_errors(value[key], child_schema, f"{path}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], f"{path}[{index}]"))
    return errors


def _parse_json(text: str) -> Any:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.split("\n", 1)[1].rsplit("```", 1)[0]
    return json.loads(cleaned)


class SemanticRuntime:
    def __init__(
        self,
        provider: ModelProvider,
        *,
        retries: RetryConfig | None = None,
        state: StateDatabase | None = None,
        prompt_root: Path | None = None,
    ) -> None:
        self.provider = provider
        self.retries = retries or RetryConfig()
        self.state = state
        self.prompt_root = prompt_root

    def _cache_key(self, task: SemanticTask, prompt: str) -> str:
        policy = {"model": self.provider.model_name, **task.model_policy}
        material = {
            "operation": task.operation,
            "prompt": prompt,
            "policy": policy,
            "content_hash": task.content_hash,
        }
        return sha256(
            json.dumps(material, sort_keys=True, default=str).encode()
        ).hexdigest()

    def run(self, task: SemanticTask) -> SemanticResult:
        instructions = (
            load_prompt(task.role, self.prompt_root) if self.prompt_root else ""
        )
        prompt = compile_context(task, instructions)
        cache_key = self._cache_key(task, prompt)
        if self.state is not None:
            cached = self.state.get_semantic_cache(cache_key)
            if cached is not None:
                return SemanticResult(cached, 0, True, prompt)

        last_error = "no attempts made"
        current_prompt = prompt
        for attempt in range(1, self.retries.max_attempts + 1):
            started = time.monotonic()
            try:
                response = self.provider.generate(
                    current_prompt,
                    schema=task.output_schema,
                    temperature=task.model_policy.get("temperature"),
                    max_tokens=task.model_policy.get("max_tokens"),
                    timeout_seconds=task.model_policy.get("timeout_seconds"),
                )
                value = _parse_json(response.text)
                if task.output_schema is not None:
                    errors = _schema_errors(value, task.output_schema)
                    if errors:
                        raise SemanticError("; ".join(errors))
                if self.state is not None:
                    self.state.record_model_call(
                        provider=self.provider.provider_name,
                        model=self.provider.model_name,
                        prompt=current_prompt,
                        output=value,
                        input_tokens=response.input_tokens,
                        output_tokens=response.output_tokens,
                        duration_ms=(time.monotonic() - started) * 1000,
                        attempt=attempt,
                    )
                    self.state.put_semantic_cache(
                        cache_key,
                        task.operation,
                        task.model_policy,
                        task.content_hash,
                        value,
                    )
                return SemanticResult(value, attempt, False, current_prompt)
            except (ModelProviderError, json.JSONDecodeError, SemanticError) as error:
                last_error = str(error)
                if attempt < self.retries.max_attempts:
                    current_prompt = (
                        f"{prompt}\n\nREPAIR ATTEMPT {attempt + 1}: Previous output was invalid: {last_error}. "
                        "Return only JSON matching the schema."
                    )
                    delay = self.retries.initial_delay_seconds * (
                        self.retries.backoff_multiplier ** (attempt - 1)
                    )
                    if delay:
                        time.sleep(delay)
        raise SemanticError(
            f"semantic operation {task.operation} failed after "
            f"{self.retries.max_attempts} attempts: {last_error}"
        )
