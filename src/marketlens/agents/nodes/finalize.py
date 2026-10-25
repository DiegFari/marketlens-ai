"""Finalize: decide the outcome and render the document. Deterministic; no LLM."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from marketlens.agents.dependencies import AgentDependencies
from marketlens.agents.instrumentation import StepContext
from marketlens.agents.rendering import determine_status, render_markdown
from marketlens.agents.state import BriefingState


async def finalize(
    state: BriefingState, ctx: StepContext, deps: AgentDependencies
) -> dict[str, Any]:
    status = determine_status(state)
    generated_at = datetime.now(UTC)
    markdown = render_markdown(
        state.model_copy(update={"status": status, "generated_at": generated_at})
    )
    ctx.detail = f"status={status.value}"
    return {"status": status, "generated_at": generated_at, "report_markdown": markdown}
