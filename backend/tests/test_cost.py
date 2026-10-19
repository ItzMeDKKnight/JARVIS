"""Cost accounting from config list prices."""

from __future__ import annotations

import logging

import pytest

from jarvis.config import JarvisConfig
from jarvis.cost import CostTracker
from jarvis.llm.types import Usage


def test_haiku_turn_is_priced_per_token_type(config: JarvisConfig) -> None:
    tracker = CostTracker(config.pricing)
    usage = Usage(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_creation_input_tokens=1_000_000,
        cache_read_input_tokens=1_000_000,
    )
    # $0.10 input + $0.50 output + $0.125 cache write (1.25x) + $0.01 cache read
    assert tracker.price("claude-haiku-5-5", usage) == pytest.approx(0.735)


def test_web_searches_are_added(config: JarvisConfig) -> None:
    tracker = CostTracker(config.pricing)
    assert tracker.price("claude-sonnet-5-5", Usage(), web_searches=3) == pytest.approx(0.03)


def test_session_totals_and_suffixed_model_ids(config: JarvisConfig) -> None:
    tracker = CostTracker(config.pricing)
    tracker.record("claude-sonnet-5-5", Usage(output_tokens=1_000_000))
    tracker.record("claude-sonnet-5-5-20261001", Usage(output_tokens=1_000_000))
    assert tracker.session_usd == pytest.approx(20.0)


def test_unknown_model_costs_zero_and_warns_once(
    config: JarvisConfig, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = CostTracker(config.pricing)
    with caplog.at_level(logging.WARNING):
        tracker.record("mystery-model", Usage(input_tokens=10))
        tracker.record("mystery-model", Usage(input_tokens=10))
    assert tracker.session_usd == 0.0
    assert sum("no price configured" in r.message for r in caplog.records) == 1


def test_session_log_reports_cache_hit_ratio(
    config: JarvisConfig, caplog: pytest.LogCaptureFixture
) -> None:
    tracker = CostTracker(config.pricing)
    tracker.record("claude-haiku-5-5", Usage(input_tokens=100, cache_read_input_tokens=900))
    with caplog.at_level(logging.INFO):
        tracker.log_session()
    usage_line = next(r for r in caplog.records if r.message == "session usage")
    assert usage_line.cache_hit_ratio == 0.9  # type: ignore[attr-defined]
    assert any(r.message == "session cost" for r in caplog.records)


# -- strict session budget -------------------------------------------------------------


def test_worst_case_counts_full_output_and_pessimistic_input(config: JarvisConfig) -> None:
    tracker = CostTracker(config.pricing, config.budget)
    # Sonnet: 8000 output x $10/M = $0.08; 2000 chars / 2 = 1000 tokens x $2.50/M (cache write).
    worst = tracker.worst_case("claude-sonnet-5-5", max_output_tokens=8000, input_chars=2000)
    assert worst == pytest.approx(0.08 + 0.0025)


def test_unknown_model_is_priced_as_the_most_expensive(config: JarvisConfig) -> None:
    tracker = CostTracker(config.pricing, config.budget)
    sonnet = tracker.worst_case("claude-sonnet-5-5", max_output_tokens=1000, input_chars=0)
    assert tracker.worst_case("mystery", max_output_tokens=1000, input_chars=0) == sonnet


def test_can_afford_and_single_warning(config: JarvisConfig) -> None:
    budget = config.budget.model_copy(update={"session_cap_usd": 1.0, "warn_at_fraction": 0.5})
    tracker = CostTracker(config.pricing, budget)
    assert tracker.can_afford(1.0)
    tracker.record("claude-sonnet-5-5", Usage(output_tokens=60_000))  # $0.60
    assert not tracker.can_afford(0.5)
    assert tracker.can_afford(0.4)
    assert tracker.take_budget_warning()
    assert not tracker.take_budget_warning()  # only once


def test_no_budget_means_no_cap(config: JarvisConfig) -> None:
    tracker = CostTracker(config.pricing)
    assert tracker.can_afford(1e9) and not tracker.take_budget_warning()


async def test_search_refuses_when_the_budget_cannot_cover_it(config: JarvisConfig) -> None:
    from typing import cast

    import anthropic

    from jarvis.search.anthropic_search import AnthropicWebSearch
    from jarvis.search.base import SearchError

    budget = config.budget.model_copy(update={"session_cap_usd": 0.01})
    search = AnthropicWebSearch(
        cast(anthropic.AsyncAnthropic, None), config.search, CostTracker(config.pricing, budget)
    )
    # 3 searches alone are $0.03: refused before any network call (the client is None).
    with pytest.raises(SearchError, match="spending limit"):
        await search.search("weather in Mumbai")


async def test_consolidation_skipped_when_over_budget(config: JarvisConfig) -> None:
    from typing import cast

    import anthropic

    from jarvis.memory.summary import AnthropicConsolidator, ConsolidationError, TurnLine

    budget = config.budget.model_copy(update={"session_cap_usd": 0.000001})
    consolidator = AnthropicConsolidator(
        cast(anthropic.AsyncAnthropic, None),
        config.memory,
        user_name="Tony",
        assistant_name="JARVIS",
        cost=CostTracker(config.pricing, budget),
    )
    with pytest.raises(ConsolidationError, match="spending limit"):
        await consolidator.consolidate(None, [], [TurnLine("user", "hi")])
