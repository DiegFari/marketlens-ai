from __future__ import annotations

import json
import logging

import pytest

from marketlens.core.logging import (
    ConsoleFormatter,
    JsonFormatter,
    RequestIdFilter,
    configure_logging,
    redact,
    request_id_ctx,
)


def _record(message: str, **extra: object) -> logging.LogRecord:
    record = logging.LogRecord("marketlens.test", logging.INFO, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(record, key, value)
    RequestIdFilter().filter(record)
    return record


def test_json_formatter_includes_request_id_and_extras() -> None:
    token = request_id_ctx.set("req-123")
    try:
        record = _record("hello", ticker="MSFT")
    finally:
        request_id_ctx.reset(token)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "hello"
    assert payload["level"] == "INFO"
    assert payload["request_id"] == "req-123"
    assert payload["ticker"] == "MSFT"
    assert payload["timestamp"].endswith("+00:00")


def test_json_formatter_serialises_exceptions() -> None:
    try:
        raise RuntimeError("boom")
    except RuntimeError:
        record = logging.LogRecord(
            "x", logging.ERROR, __file__, 1, "failed", None, exc_info=__import__("sys").exc_info()
        )

    payload = json.loads(JsonFormatter().format(record))

    assert "RuntimeError: boom" in payload["exception"]


def test_console_formatter_is_human_readable() -> None:
    line = ConsoleFormatter().format(_record("hello", ticker="NVDA"))

    assert "INFO" in line
    assert "[-]" in line  # no active request
    assert line.endswith("hello ticker=NVDA")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("GET https://x.test/quote?apikey=SECRET&symbol=MSFT", "apikey=***&symbol=MSFT"),
        ("headers: api-key: SECRET", "api-key: ***"),
        ("token=SECRET done", "token=*** done"),
        ("nothing to hide here", "nothing to hide here"),
    ],
)
def test_redact_masks_credentials(raw: str, expected: str) -> None:
    assert expected in redact(raw)
    assert "SECRET" not in redact(raw)


def test_handler_redacts_third_party_url_logging(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG", "json")
    logging.getLogger("httpx").setLevel(logging.DEBUG)  # a developer debugging httpx

    logging.getLogger("httpx").info("HTTP Request: GET %s", "https://x.test/q?apikey=SECRET")

    out = capsys.readouterr().out
    assert "SECRET" not in out
    assert "apikey=***" in out


def test_configure_logging_emits_json_to_stdout(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO", "json")

    logging.getLogger("marketlens.test").info("structured", extra={"step": 1})

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert payload["message"] == "structured"
    assert payload["step"] == 1
