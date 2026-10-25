"""Streamlit entry point. Start with ``python -m marketlens.ui``."""

from __future__ import annotations

import logging
from typing import Any

import streamlit as st

from marketlens.core.logging import configure_logging
from marketlens.ui.client import ApiError, MarketLensClient
from marketlens.ui.config import get_ui_settings

logger = logging.getLogger("marketlens.ui")

DISCLAIMER = (
    "MarketLens AI produces research briefings, not price predictions or investment "
    "advice. Always verify information against primary sources."
)


@st.cache_resource
def get_client() -> MarketLensClient:
    settings = get_ui_settings()
    configure_logging(settings.log_level, settings.log_format)
    return MarketLensClient(settings.api_base_url, timeout=settings.request_timeout_seconds)


def render_sidebar(client: MarketLensClient) -> None:
    with st.sidebar:
        st.header("System status")
        try:
            status = client.readiness()
        except ApiError as exc:
            st.error(f"API unavailable: {exc.message}")
            return
        st.success(f"API online · v{status['version']} · {status['environment']}")
        for name, configured in status["integrations"].items():
            st.write(f"{'✅' if configured else '⚪'} {name.replace('_', ' ')}")


def render_analysis_form(client: MarketLensClient) -> None:
    with st.form("analysis"):
        ticker = st.text_input("Ticker", placeholder="e.g. MSFT, NVDA, ASML", max_chars=16)
        submitted = st.form_submit_button("Generate briefing", type="primary")

    if not submitted:
        return
    if not ticker.strip():
        st.warning("Enter a ticker symbol.")
        return

    with st.spinner(f"Agents researching {ticker.strip().upper()}… (about a minute)"):
        try:
            result = client.analyze(ticker)
        except ApiError as exc:
            if exc.code == "validation_error":
                st.warning("That doesn't look like a valid ticker (e.g. AAPL, MSFT, NVDA).")
            elif exc.code in ("ticker_not_found", "insufficient_data", "llm_not_configured"):
                st.warning(exc.message)
            else:
                st.error(f"{exc.message} (request id: {exc.request_id or 'n/a'})")
                logger.warning("analysis failed", extra={"code": exc.code})
            return

    st.markdown(result.get("report_markdown", ""))
    render_trace(result)


def render_trace(result: dict[str, Any]) -> None:
    """Show how the agents produced the briefing: one row per workflow step."""
    trace = result.get("trace") or []
    if not trace:
        return
    with st.expander(f"How this briefing was produced · {result.get('model', '')}"):
        st.dataframe(
            [
                {
                    "step": e["step"],
                    "status": e["status"],
                    "seconds": round(e["duration_ms"] / 1000, 1),
                    "llm calls": e["llm_calls"],
                    "tokens": e["input_tokens"] + e["output_tokens"],
                    "tools": ", ".join(e["tool_calls"]),
                    "detail": e.get("detail") or "",
                }
                for e in trace
            ],
            hide_index=True,
            use_container_width=True,
        )


def main() -> None:
    st.set_page_config(page_title="MarketLens AI", page_icon="📈", layout="wide")
    st.title("📈 MarketLens AI")
    st.caption("Multi-agent market intelligence briefings")

    client = get_client()
    render_sidebar(client)
    render_analysis_form(client)

    st.divider()
    st.caption(DISCLAIMER)


if __name__ == "__main__":
    main()
