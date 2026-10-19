"""web_search: look up current information and show the sources on screen."""

from __future__ import annotations

from pydantic import Field

from jarvis.events import SourceItem, Sources
from jarvis.search.base import SearchError, SearchProvider
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome


class WebSearchInput(ToolInput):
    query: str = Field(
        min_length=1,
        max_length=300,
        description="A focused search query, e.g. 'weather in Mumbai today'.",
    )


class WebSearchTool(Tool[WebSearchInput]):
    name = "web_search"
    description = (
        "Search the web for current or changing information. Call this whenever the answer "
        "depends on things like weather, news, prices, scores, schedules or recent releases, "
        "or when you are not certain of a fact. Returns a short summary; the sources are shown "
        "on the user's screen automatically."
    )
    input_model = WebSearchInput

    def __init__(self, provider: SearchProvider, *, timeout_s: float) -> None:
        self._provider = provider
        self.timeout_s = timeout_s

    async def run(self, args: WebSearchInput, ctx: ToolContext) -> ToolOutcome:
        try:
            result = await self._provider.search(args.query)
        except SearchError as exc:
            return ToolOutcome(content=f"Search failed: {exc}.", ok=False, summary="Search failed")

        if result.sources:
            items = tuple(SourceItem(title=s.title, url=s.url) for s in result.sources)
            await ctx.bus.publish(Sources(items=items))
        titles = "; ".join(s.title for s in result.sources) or "none"
        return ToolOutcome(
            content=f"Summary: {result.summary}\nSources shown on screen: {titles}",
            ok=True,
            summary=f"Found {len(result.sources)} sources",
            untrusted=True,
        )
