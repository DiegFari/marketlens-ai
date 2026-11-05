"""Streamlit entry point. Start with ``python -m marketlens.ui``.

A chat: the visitor writes a request ("can you give me a brief on AMZN?"), the API
resolves it to a ticker, and the agents produce the briefing.
"""

from __future__ import annotations

import hmac
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
WELCOME = (
    "Hi! Ask me for a market briefing on any listed company, for example "
    '*"can you give me a brief on AMZN?"*, *"what\'s going on with Nvidia?"* or just `MSFT`.'
)
EXAMPLES = ["Brief me on AMZN", "What's going on with Nvidia?", "MSFT"]


@st.cache_resource
def get_client() -> MarketLensClient:
    settings = get_ui_settings()
    configure_logging(settings.log_level, settings.log_format)
    return MarketLensClient(settings.api_base_url, timeout=settings.request_timeout_seconds)


def password_ok() -> bool:
    """Gate the app behind ``UI_ACCESS_PASSWORD`` when one is configured."""
    password = get_ui_settings().access_password
    if password is None or st.session_state.get("authenticated"):
        return True
    with st.form("login"):
        attempt = st.text_input("Password", type="password")
        if st.form_submit_button("Enter"):
            if hmac.compare_digest(attempt, password.get_secret_value()):
                st.session_state.authenticated = True
                st.rerun()
            st.error("Wrong password.")
    return False


def render_sidebar(client: MarketLensClient) -> None:
    with st.sidebar:
        st.header("System status")
        try:
            status = client.readiness()
        except ApiError as exc:
            st.error(f"API unavailable: {exc.message}")
        else:
            st.success(f"API online · v{status['version']} · {status['environment']}")
            for name, configured in status["integrations"].items():
                st.write(f"{'✅' if configured else '⚪'} {name.replace('_', ' ')}")
        st.divider()
        if st.button("New conversation", width="stretch"):
            st.session_state.messages = []
            st.rerun()


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
            width="stretch",
        )


def render_message(message: dict[str, Any]) -> None:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message.get("result"):
            render_trace(message["result"])


def answer(client: MarketLensClient, prompt: str) -> dict[str, Any]:
    """Resolve the request, run the briefing, and return the assistant message."""
    with st.status("Understanding your request…", expanded=False) as status:
        try:
            resolved = client.resolve(prompt)
        except ApiError as exc:
            status.update(label="Something went wrong", state="error")
            return _error_message(exc)

        ticker = resolved.get("ticker")
        if not ticker:
            status.update(label="Need a bit more detail", state="complete")
            return {"role": "assistant", "content": resolved.get("clarification") or ""}

        name = resolved.get("company_name")
        label = f"{name} ({ticker})" if name else ticker
        status.update(label=f"Agents researching {label}… (about a minute)", state="running")
        try:
            result = client.analyze(ticker)
        except ApiError as exc:
            status.update(label=f"Couldn't brief {ticker}", state="error")
            return _error_message(exc, ticker)
        status.update(label=f"Briefing ready: {label}", state="complete")

    return {"role": "assistant", "content": result.get("report_markdown", ""), "result": result}


def _error_message(exc: ApiError, ticker: str | None = None) -> dict[str, Any]:
    if exc.code == "ticker_not_found":
        text = f"I couldn't find market data for **{ticker}**. Is that the right ticker?"
    elif exc.code in ("validation_error", "insufficient_data", "llm_not_configured"):
        text = exc.message
    else:
        logger.warning("request failed", extra={"code": exc.code})
        text = f"⚠️ {exc.message} (request id: {exc.request_id or 'n/a'})"
    return {"role": "assistant", "content": text}


def main() -> None:
    st.set_page_config(page_title="MarketLens AI", page_icon="📈", layout="wide")
    st.title("📈 MarketLens AI")
    st.caption("Multi-agent market intelligence briefings")

    if not password_ok():
        return

    client = get_client()
    render_sidebar(client)

    messages: list[dict[str, Any]] = st.session_state.setdefault("messages", [])
    render_message({"role": "assistant", "content": WELCOME})
    if not messages:
        columns = st.columns(len(EXAMPLES))
        for column, example in zip(columns, EXAMPLES, strict=True):
            if column.button(example, width="stretch"):
                st.session_state.pending = example
    for message in messages:
        render_message(message)

    prompt = st.chat_input('Ask for a briefing, e.g. "brief me on AMZN"', max_chars=300)
    prompt = prompt or st.session_state.pop("pending", None)
    if prompt:
        user_message = {"role": "user", "content": prompt}
        messages.append(user_message)
        render_message(user_message)
        reply = answer(client, prompt)
        messages.append(reply)
        render_message(reply)

    st.caption(DISCLAIMER)


if __name__ == "__main__":
    main()
