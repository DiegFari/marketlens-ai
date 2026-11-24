"""Run real briefings end to end and save them as documentation examples.

Uses your ``.env`` (LLM_PROVIDER, model key, FMP and NewsAPI keys) and the real
workflow, exactly as the API would. For each ticker it writes:

* ``docs/examples/<TICKER>.md``: the briefing as a reader sees it
* ``docs/examples/runs.json``: status, warnings, timings, tokens and fallbacks per run

    python scripts/generate_examples.py                 # MSFT NVDA ASML SAP
    python scripts/generate_examples.py MSFT

Cost: per ticker ~5 FMP calls, 1-2 NewsAPI calls and 4-6 model calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from marketlens.agents import (
    AgentDependencies,
    BriefingState,
    BriefingWorkflow,
    WorkflowTimeoutError,
)
from marketlens.core.config import Settings
from marketlens.core.logging import configure_logging
from marketlens.llm import build_llm_client
from marketlens.services.registry import DataProviders

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "docs" / "examples"
DEFAULT_TICKERS = ["MSFT", "NVDA", "ASML", "SAP"]


def summarise(state: BriefingState, seconds: float) -> dict[str, Any]:
    trace = state.trace
    return {
        "ticker": state.ticker,
        "status": state.status.value if state.status else None,
        "seconds": round(seconds, 1),
        "llm_calls": sum(e.llm_calls for e in trace),
        "input_tokens": sum(e.input_tokens for e in trace),
        "output_tokens": sum(e.output_tokens for e in trace),
        "tool_calls": [t for e in trace for t in e.tool_calls],
        "structured_fallbacks": sum(e.structured_fallbacks for e in trace),
        "failed_steps": [e.step for e in trace if e.status == "failed"],
        "warnings": [w.code for w in state.warnings],
        "developments": len(state.news_analysis.developments) if state.news_analysis else 0,
        "irrelevant_articles": (
            len(state.news_analysis.irrelevant_article_ids) if state.news_analysis else 0
        ),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("tickers", nargs="*", default=DEFAULT_TICKERS)
    parser.add_argument("--pause", type=float, default=45, help="seconds between tickers")
    args = parser.parse_args()

    settings = Settings()
    configure_logging("WARNING", "console")
    if not settings.llm_is_configured:
        print(f"LLM_PROVIDER={settings.llm.provider} is not configured (see README setup).")
        return 2

    llm = build_llm_client(settings)
    providers = DataProviders.from_settings(settings.data_providers)
    workflow = BriefingWorkflow(
        AgentDependencies(llm, providers.market_data, providers.news, settings.agents)
    )
    EXAMPLES.mkdir(parents=True, exist_ok=True)

    runs = []
    try:
        for i, ticker in enumerate(t.upper() for t in args.tickers):
            if i:
                # Free tiers limit tokens per minute; let the window reset between runs.
                await asyncio.sleep(args.pause)
            print(f"→ {ticker} …", end=" ", flush=True)
            start = asyncio.get_running_loop().time()
            try:
                state = await workflow.run(ticker)
            except WorkflowTimeoutError as exc:
                print(f"timed out ({exc}); skipped")
                continue
            run = summarise(state, asyncio.get_running_loop().time() - start)
            runs.append(run)
            (EXAMPLES / f"{ticker}.md").write_text(state.report_markdown or "")
            print(
                f"{run['status']} in {run['seconds']}s · {run['llm_calls']} LLM calls · "
                f"{run['input_tokens'] + run['output_tokens']} tokens · "
                f"fallbacks={run['structured_fallbacks']} · failed={run['failed_steps']}"
            )
    finally:
        await providers.aclose()

    (EXAMPLES / "runs.json").write_text(
        json.dumps({"model": llm.model_name, "runs": runs}, indent=2) + "\n"
    )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
