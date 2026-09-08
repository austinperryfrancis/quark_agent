"""Structured JSON and human-readable console logging."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...] = ()) -> None:
        super().__init__()
        self.secrets = secrets

    def _clean(self, value: Any) -> Any:
        if isinstance(value, str):
            for secret in self.secrets:
                if secret:
                    value = value.replace(secret, "[REDACTED]")
            return value
        if isinstance(value, dict):
            return {key: self._clean(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self._clean(item) for item in value]
        return value

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self._clean(record.getMessage()),
        }
        payload.update(
            (key, self._clean(value))
            for key, value in record.__dict__.items()
            if key not in _STANDARD and not key.startswith("_")
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets: tuple[str, ...]) -> None:
        super().__init__("%(levelname)s %(name)s: %(message)s")
        self.secrets = secrets

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        for secret in self.secrets:
            if secret:
                rendered = rendered.replace(secret, "[REDACTED]")
        return rendered


def configure_logging(
    level: str = "INFO", output_format: str = "console", redact: tuple[str, ...] = ()
) -> None:
    handler = logging.StreamHandler()
    formatter: logging.Formatter = (
        JsonFormatter(redact) if output_format == "json" else RedactingFormatter(redact)
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
