import json
import logging

from quark.logging import JsonFormatter, RedactingFormatter


def test_json_logging_redacts_configured_values() -> None:
    record = logging.LogRecord(
        "quark", logging.INFO, __file__, 1, "vault=%s", ("secret",), None
    )
    rendered = JsonFormatter(("secret",)).format(record)

    payload = json.loads(rendered)
    assert payload["message"] == "vault=[REDACTED]"
    assert "secret" not in rendered


def test_console_logging_redacts_configured_values() -> None:
    record = logging.LogRecord(
        "quark", logging.INFO, __file__, 1, "token=%s", ("secret",), None
    )

    assert "secret" not in RedactingFormatter(("secret",)).format(record)
