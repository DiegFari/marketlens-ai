"""Logging setup.

* ``json`` format: one JSON object per line on stdout. App Service forwards stdout to
  Log Analytics / Application Insights, where each field becomes queryable.
* ``console`` format: human-readable lines for local development.

Every record carries the current ``request_id`` (set by the API middleware), so all
log lines produced while serving a request — including those from agents and tool
calls in later phases — can be correlated.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)

# Attributes every LogRecord has; anything else was passed through ``extra=``.
_STANDARD_ATTRS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
    | {"message", "asctime", "request_id", "taskName", "color_message"}
)

_NOISY_LOGGERS = ("httpx", "httpcore", "urllib3", "watchdog")


def _extra_fields(record: logging.LogRecord) -> dict[str, Any]:
    return {k: v for k, v in record.__dict__.items() if k not in _STANDARD_ATTRS}


class RequestIdFilter(logging.Filter):
    """Attach the active request id to every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


# Credential-looking query parameters and headers, e.g. "apikey=abc" or "api-key: abc".
_SECRET_PATTERN = re.compile(
    r"(?i)\b(api[_-]?key|apikey|access[_-]?token|token|sig|password)(\s*[=:]\s*)[^\s&\"',]+"
)


def redact(text: str) -> str:
    return _SECRET_PATTERN.sub(r"\1\2***", text)


class RedactSecretsFilter(logging.Filter):
    """Defence in depth: mask credentials in messages, whatever logger produced them.

    Our clients keep keys out of URLs and messages; this catches third-party libraries
    (e.g. httpx request lines) if someone turns their logging up while debugging.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", None),
        }
        payload.update(_extra_fields(record))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class ConsoleFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-8s %(name)s [%(request_id)s] %(message)s",
            datefmt="%H:%M:%S",
        )

    def formatMessage(self, record: logging.LogRecord) -> str:  # noqa: N802 (stdlib name)
        if getattr(record, "request_id", None) is None:
            record.request_id = "-"
        line = super().formatMessage(record)
        extras = _extra_fields(record)
        if extras:
            line += " " + " ".join(f"{key}={value}" for key, value in extras.items())
        return line


def configure_logging(level: str = "INFO", fmt: str = "console") -> None:
    """Configure the root logger. Safe to call more than once."""
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RequestIdFilter())
    handler.addFilter(RedactSecretsFilter())
    handler.setFormatter(JsonFormatter() if fmt == "json" else ConsoleFormatter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Route uvicorn through our handler; our middleware replaces its access log.
    for name in ("uvicorn", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        uvicorn_logger.handlers.clear()
        uvicorn_logger.propagate = True
    logging.getLogger("uvicorn.access").disabled = True

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
