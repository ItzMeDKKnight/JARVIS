"""Search provider interface.

The web_search tool depends only on this interface, so the Anthropic-hosted
search can be swapped for another provider (or a fake in tests) without
touching the tool or the conversation logic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class SearchError(Exception):
    """Search failed; the message is safe to pass to the model."""


@dataclass(frozen=True)
class SearchSource:
    title: str
    url: str


@dataclass(frozen=True)
class SearchResult:
    """A short summary ready to be paraphrased aloud, plus the pages it came from."""

    summary: str
    sources: tuple[SearchSource, ...]


class SearchProvider(Protocol):
    async def search(self, query: str) -> SearchResult:
        """Search the web for ``query``; raise SearchError on failure."""
        ...
