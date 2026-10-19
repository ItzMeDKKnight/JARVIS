"""Local token and cost accounting for every Claude call Jarvis makes.

Each call (conversation, web search, memory consolidation) reports its usage
here. The tracker prices it from the list prices in config.yaml and keeps
per-session totals, which are written to the local log when Jarvis exits.

The numbers are an estimate at list price, useful for noticing a cost
regression (say, prompt caching silently breaking). Anthropic's console
remains the source of truth for billing.

It also enforces the per-session budget (``budget.session_cap_usd``). The
cap is strict: before every call, the caller asks :meth:`CostTracker.can_afford`
with a *worst-case* estimate (full ``max_tokens`` of output, input counted
pessimistically, no cache discount), and the call is skipped if that could
cross the cap. So actual spending stays at or below it, never "slightly over".
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from jarvis.config import BudgetConfig, ModelPrice, PricingConfig
from jarvis.llm.types import Usage

logger = logging.getLogger(__name__)

_PER_MILLION = 1_000_000
_PER_THOUSAND = 1_000


@dataclass
class _Totals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0
    web_searches: int = 0
    usd: float = 0.0

    def add(self, usage: Usage, web_searches: int, usd: float) -> None:
        self.calls += 1
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_creation_input_tokens += usage.cache_creation_input_tokens
        self.cache_read_input_tokens += usage.cache_read_input_tokens
        self.web_searches += web_searches
        self.usd += usd

    def cache_hit_ratio(self) -> float:
        """Share of prompt tokens served from cache: the proof that caching works."""
        prompt = self.input_tokens + self.cache_creation_input_tokens + self.cache_read_input_tokens
        return self.cache_read_input_tokens / prompt if prompt else 0.0


def approx_chars(value: object) -> int:
    """Rough size of request content (dicts, lists, strings, SDK blocks) in characters."""
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        return sum(len(str(k)) + approx_chars(v) for k, v in value.items())
    if isinstance(value, list | tuple):
        return sum(approx_chars(v) for v in value)
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return approx_chars(dump())
    return len(json.dumps(value, default=str))


@dataclass
class CostTracker:
    pricing: PricingConfig
    budget: BudgetConfig | None = None
    _by_model: dict[str, _Totals] = field(default_factory=dict)
    _warned: set[str] = field(default_factory=set)
    _budget_warning_given: bool = False

    def _price_for(self, model: str) -> ModelPrice | None:
        price = self.pricing.models.get(model)
        if price is None:
            # Served model ids can carry a suffix (e.g. a dated snapshot); match on the prefix.
            price = next((p for m, p in self.pricing.models.items() if model.startswith(m)), None)
        if price is None and model not in self._warned:
            self._warned.add(model)
            logger.warning(
                "no price configured for model; cost not counted", extra={"model": model}
            )
        return price

    def price(self, model: str, usage: Usage, *, web_searches: int = 0) -> float:
        """USD for one call at list price (0.0 when the model has no configured price)."""
        price = self._price_for(model)
        if price is None:
            return 0.0
        cache_write = price.input * self.pricing.cache_write_multiplier
        tokens_usd = (
            usage.input_tokens * price.input
            + usage.output_tokens * price.output
            + usage.cache_creation_input_tokens * cache_write
            + usage.cache_read_input_tokens * price.cache_read
        ) / _PER_MILLION
        return tokens_usd + web_searches * self.pricing.web_search_per_1000 / _PER_THOUSAND

    def record(self, model: str, usage: Usage, *, web_searches: int = 0) -> float:
        """Add one call to the session totals and return its estimated cost in USD."""
        usd = self.price(model, usage, web_searches=web_searches)
        self._by_model.setdefault(model, _Totals()).add(usage, web_searches, usd)
        return usd

    @property
    def session_usd(self) -> float:
        return sum(t.usd for t in self._by_model.values())

    # -- budget -------------------------------------------------------------------------

    @property
    def cap_usd(self) -> float | None:
        return None if self.budget is None else self.budget.session_cap_usd

    def worst_case(
        self,
        model: str,
        *,
        max_output_tokens: int,
        input_chars: int,
        extra_input_tokens: int = 0,
        web_searches: int = 0,
    ) -> float:
        """Upper bound on what one call could cost, for the budget check.

        A model without a configured price is assumed to cost as much as the
        most expensive one configured, so a typo can't switch the cap off.
        """
        price = self._price_for(model) or max(
            self.pricing.models.values(), key=lambda p: p.input + p.output
        )
        chars_per_token = self.budget.chars_per_token_estimate if self.budget else 1.0
        input_tokens = input_chars / chars_per_token + extra_input_tokens
        # Cache writes cost more than plain input, so price all input as a cache write.
        input_price = price.input * self.pricing.cache_write_multiplier
        tokens_usd = (input_tokens * input_price + max_output_tokens * price.output) / _PER_MILLION
        return tokens_usd + web_searches * self.pricing.web_search_per_1000 / _PER_THOUSAND

    def can_afford(self, worst_case_usd: float) -> bool:
        """Whether a call costing up to ``worst_case_usd`` keeps the session within the cap."""
        cap = self.cap_usd
        if cap is None:
            return True
        affordable = self.session_usd + worst_case_usd <= cap
        if not affordable:
            logger.warning(
                "session budget would be exceeded; call skipped",
                extra={
                    "session_cost_usd": round(self.session_usd, 6),
                    "worst_case_usd": round(worst_case_usd, 6),
                    "cap_usd": cap,
                },
            )
        return affordable

    def take_budget_warning(self) -> bool:
        """True exactly once, the first time spending passes the warning threshold."""
        if self.budget is None or self._budget_warning_given:
            return False
        if self.session_usd < self.budget.session_cap_usd * self.budget.warn_at_fraction:
            return False
        self._budget_warning_given = True
        return True

    def log_session(self) -> None:
        """One log line per model plus a total, written when Jarvis exits."""
        for model, totals in sorted(self._by_model.items()):
            logger.info(
                "session usage",
                extra={
                    "model": model,
                    "calls": totals.calls,
                    "input_tokens": totals.input_tokens,
                    "output_tokens": totals.output_tokens,
                    "cache_creation_input_tokens": totals.cache_creation_input_tokens,
                    "cache_read_input_tokens": totals.cache_read_input_tokens,
                    "cache_hit_ratio": round(totals.cache_hit_ratio(), 3),
                    "web_searches": totals.web_searches,
                    "cost_usd": round(totals.usd, 6),
                },
            )
        logger.info("session cost", extra={"cost_usd": round(self.session_usd, 6)})
