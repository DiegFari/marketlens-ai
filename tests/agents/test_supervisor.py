"""The supervisor's routing policy, rule by rule (pure function, no graph needed)."""

from __future__ import annotations

from typing import Any

import pytest

from marketlens.agents.instrumentation import StepContext
from marketlens.agents.nodes.supervisor import plan_next_steps, supervisor
from marketlens.agents.state import BriefingState, Step, StepStatus
from tests.agent_fakes import make_deps, make_feed, make_profile, make_snapshot

S, F, K = StepStatus.SUCCEEDED, StepStatus.FAILED, StepStatus.SKIPPED
pytestmark = pytest.mark.anyio


def _state(**kwargs: Any) -> BriefingState:
    return BriefingState(ticker="MSFT", **kwargs)


def _collected(**kwargs: Any) -> BriefingState:
    defaults: dict[str, Any] = {"snapshot": make_snapshot(), "news": make_feed()}
    defaults.update(kwargs)
    status = {Step.COLLECT_DATA: S, **defaults.pop("step_status", {})}
    return _state(step_status=status, **defaults)


def test_first_turn_collects_data() -> None:
    assert plan_next_steps(_state(), max_turns=8).next_steps == [Step.COLLECT_DATA]


def test_both_analysts_run_in_parallel_when_inputs_exist() -> None:
    decision = plan_next_steps(_collected(), max_turns=8)

    assert decision.next_steps == [Step.MARKET_ANALYST, Step.NEWS_ANALYST]
    assert "parallel" in decision.reason


def test_news_analyst_skipped_without_articles() -> None:
    decision = plan_next_steps(_collected(news=make_feed(count=0)), max_turns=8)

    assert decision.next_steps == [Step.MARKET_ANALYST]
    assert Step.NEWS_ANALYST in decision.skipped


def test_market_analyst_skipped_for_profile_only_company() -> None:
    # e.g. ASML on FMP's free plan: profile available, no price or metrics.
    decision = plan_next_steps(_collected(snapshot=None, profile=make_profile()), max_turns=8)

    assert decision.next_steps == [Step.NEWS_ANALYST]
    assert decision.skipped == {Step.MARKET_ANALYST: "no price or financial data"}


def test_report_follows_analysts_even_if_one_failed() -> None:
    state = _collected(step_status={Step.MARKET_ANALYST: S, Step.NEWS_ANALYST: F})

    assert plan_next_steps(state, max_turns=8).next_steps == [Step.REPORT_WRITER]


def test_report_written_from_news_alone() -> None:
    state = _collected(snapshot=None, step_status={Step.MARKET_ANALYST: K, Step.NEWS_ANALYST: S})

    assert plan_next_steps(state, max_turns=8).next_steps == [Step.REPORT_WRITER]


def test_finalize_after_report_attempt_whatever_its_outcome() -> None:
    for outcome in (S, F):
        state = _collected(
            step_status={Step.MARKET_ANALYST: S, Step.NEWS_ANALYST: S, Step.REPORT_WRITER: outcome}
        )
        assert plan_next_steps(state, max_turns=8).next_steps == [Step.FINALIZE]


def test_unknown_ticker_finalizes_immediately() -> None:
    state = _collected(snapshot=None, news=None, ticker_not_found=True)

    assert plan_next_steps(state, max_turns=8).next_steps == [Step.FINALIZE]


def test_no_data_at_all_skips_report() -> None:
    decision = plan_next_steps(_collected(snapshot=None, news=None), max_turns=8)

    assert decision.next_steps == [Step.FINALIZE]
    assert Step.REPORT_WRITER in decision.skipped


def test_collection_failure_still_reaches_finalize() -> None:
    state = _state(step_status={Step.COLLECT_DATA: F})

    assert plan_next_steps(state, max_turns=8).next_steps == [Step.FINALIZE]


def test_turn_limit_forces_finalize() -> None:
    decision = plan_next_steps(_state(supervisor_turns=8), max_turns=8)

    assert decision.next_steps == [Step.FINALIZE]
    assert decision.turn_limit_hit


async def test_supervisor_node_records_skips_and_increments_turns() -> None:
    state = _collected(news=None, supervisor_turns=1)

    update = await supervisor(state, StepContext("supervisor"), make_deps())

    assert update["next_steps"] == [Step.MARKET_ANALYST]
    assert update["supervisor_turns"] == 2
    assert update["step_status"] == {Step.NEWS_ANALYST: StepStatus.SKIPPED}


async def test_supervisor_node_warns_when_turn_limit_hit() -> None:
    update = await supervisor(
        _state(supervisor_turns=3), StepContext("supervisor"), make_deps(max_supervisor_turns=3)
    )

    assert update["next_steps"] == [Step.FINALIZE]
    assert update["warnings"][0].code == "turn_limit_reached"
