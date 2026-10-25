from __future__ import annotations

import re
from typing import Annotated, Any

from pydantic import BeforeValidator

# Covers plain US tickers (AAPL), share classes (BRK.B) and exchange suffixes (ASML.AS).
_TICKER_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{0,9}([.-][A-Z0-9]{1,4})?$")


def normalise_ticker(value: Any) -> Any:
    """Trim and upper-case a ticker, rejecting anything that cannot be a symbol."""
    if not isinstance(value, str):
        return value
    ticker = value.strip().upper()
    if not _TICKER_PATTERN.match(ticker):
        raise ValueError("ticker must look like AAPL, BRK.B or ASML.AS")
    return ticker


Ticker = Annotated[str, BeforeValidator(normalise_ticker)]
