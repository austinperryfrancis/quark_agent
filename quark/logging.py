"""Structured JSON and human-readable console logging."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            (key, value)
            for key, value in record.__dict__.items()
            if key not in _STANDARD and not key.startswith("_")
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def configure_logging(level: str = "INFO", output_format: str = "console") -> None:
    handler = logging.StreamHandler()
    formatter: logging.Formatter = (
        JsonFormatter()
        if output_format == "json"
        else logging.Formatter("%(levelname)s %(name)s: %(message)s")
    )
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
