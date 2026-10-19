"""Config-driven model routing.

Routing is a keyword match rather than an extra classification call: it costs
nothing, adds no latency before the first spoken word, and is predictable
enough to test. Rules and models live in config.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis.config import LLMConfig, RouteConfig, RouterRule
from jarvis.text import normalize_name


def _whole_word(phrase: str) -> re.Pattern[str]:
    # Lookarounds instead of \b so keywords such as "c++" and "c#" still match as whole words.
    return re.compile(rf"(?<!\w){re.escape(normalize_name(phrase))}(?!\w)")


@dataclass(frozen=True)
class _CompiledRule:
    route: str
    keywords: tuple[re.Pattern[str], ...]
    ignore: tuple[re.Pattern[str], ...]

    @classmethod
    def from_config(cls, rule: RouterRule) -> _CompiledRule:
        return cls(
            route=rule.route,
            keywords=tuple(_whole_word(k) for k in rule.keywords),
            ignore=tuple(_whole_word(p) for p in rule.ignore_phrases),
        )

    def matches(self, normalized_text: str) -> bool:
        for pattern in self.ignore:
            normalized_text = pattern.sub(" ", normalized_text)
        return any(pattern.search(normalized_text) for pattern in self.keywords)


class ModelRouter:
    """Picks a route (model + settings) for each user turn."""

    def __init__(self, config: LLMConfig) -> None:
        self._routes = config.routes
        self._default = config.router.default_route
        self._rules = [_CompiledRule.from_config(rule) for rule in config.router.rules]

    def route(self, user_text: str) -> tuple[str, RouteConfig]:
        """Return ``(route_name, route)``: first matching rule wins, else the default route."""
        text = normalize_name(user_text)
        for rule in self._rules:
            if rule.matches(text):
                return rule.route, self._routes[rule.route]
        return self._default, self._routes[self._default]
