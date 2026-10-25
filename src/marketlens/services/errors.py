"""Typed errors raised by provider clients.

Callers branch on the type, never on HTTP status codes or provider-specific messages.
The API layer maps each type to a client-facing response (see ``api/errors.py``);
agents in later phases use the same types to decide whether to degrade gracefully.
"""

from __future__ import annotations


class DataProviderError(Exception):
    """Base class. ``message`` is for logs and may contain provider wording."""

    def __init__(self, provider: str, message: str, *, status_code: int | None = None) -> None:
        super().__init__(f"[{provider}] {message}")
        self.provider = provider
        self.message = message
        self.status_code = status_code


class ProviderNotConfiguredError(DataProviderError):
    """No API key configured for a provider the request needs."""


class ProviderAuthError(DataProviderError):
    """The API key is missing, invalid or disabled."""


class ProviderPermissionError(DataProviderError):
    """The key is valid, but the subscription plan does not include this data."""


class ProviderRequestError(DataProviderError):
    """The provider rejected the request as malformed. Indicates a bug on our side."""


class TickerNotFoundError(DataProviderError):
    def __init__(self, provider: str, symbol: str) -> None:
        super().__init__(provider, f"no data found for ticker {symbol}", status_code=None)
        self.symbol = symbol


class ProviderRateLimitError(DataProviderError):
    """Rate limit or quota reached. ``retry_after`` is set when the provider says when."""

    def __init__(
        self,
        provider: str,
        message: str,
        *,
        retry_after: float | None = None,
        status_code: int | None = None,
    ) -> None:
        super().__init__(provider, message, status_code=status_code)
        self.retry_after = retry_after


class ProviderUnavailableError(DataProviderError):
    """Timeout, connection failure or 5xx. Transient; safe to retry."""


class ProviderResponseError(DataProviderError):
    """The response could not be parsed into the expected shape."""
