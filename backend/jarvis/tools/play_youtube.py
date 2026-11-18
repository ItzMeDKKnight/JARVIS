"""play_youtube: find a video on YouTube and open it so it starts playing.

How it finds the video: one Anthropic-hosted web search restricted to
youtube.com (see :class:`jarvis.search.base.VideoFinder`). Jarvis itself never
contacts YouTube; only the browser does, when the page opens. The video's
watch page is then opened in the default browser, where YouTube starts playback
on its own (subject to the browser's autoplay rules, see README).

Safety: search results are untrusted web content. Only an 11-character video ID
is taken from them, checked against YouTube's ID alphabet, and the address is
rebuilt from scratch as ``https://www.youtube.com/watch?v=<id>``. Nothing else from
a result URL reaches the browser. If no video is found, YouTube's own search
page for the request is opened instead.
"""

from __future__ import annotations

import asyncio
import re
from urllib.parse import parse_qs, quote_plus, urlsplit

from pydantic import Field

from jarvis.events import SourceItem, Sources
from jarvis.os_adapters.base import BrowserOpener, LaunchError
from jarvis.search.base import SearchError, SearchSource, VideoFinder
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome

_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})
_PATH_PREFIXES = ("/shorts/", "/live/", "/embed/")
WATCH_URL = "https://www.youtube.com/watch?v={video_id}"
RESULTS_URL = "https://www.youtube.com/results?search_query={query}"


def video_id_from_url(url: str) -> str | None:
    """The video ID in a YouTube video URL, or None for channels, playlists and other pages."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    host = (parts.hostname or "").lower()
    candidate: str | None = None
    if host == "youtu.be":
        candidate = parts.path.lstrip("/").split("/")[0]
    elif host in _YOUTUBE_HOSTS:
        if parts.path == "/watch":
            values = parse_qs(parts.query).get("v")
            candidate = values[0] if values else None
        else:
            for prefix in _PATH_PREFIXES:
                if parts.path.startswith(prefix):
                    candidate = parts.path[len(prefix) :].split("/")[0]
    if candidate and _VIDEO_ID.match(candidate):
        return candidate
    return None


def first_video(sources: tuple[SearchSource, ...]) -> tuple[str, str] | None:
    """(video_id, title) of the first result that is a single video."""
    for source in sources:
        video_id = video_id_from_url(source.url)
        if video_id is not None:
            return video_id, source.title
    return None


class PlayYouTubeInput(ToolInput):
    query: str = Field(
        min_length=1,
        max_length=200,
        description=(
            "What to play, as a YouTube search, e.g. 'Kesariya Arijit Singh official video' or "
            "'lofi hip hop for studying'. Keep the user's song, artist and language."
        ),
    )


class PlayYouTubeTool(Tool[PlayYouTubeInput]):
    name = "play_youtube"
    description = (
        "Play a video or song on YouTube in the user's default browser. Call this whenever the "
        "user asks to play, put on or watch something on YouTube, in any language (for example "
        "'play Believer on YouTube', 'YouTube pe Kesariya chalao', 'यूट्यूब पर भजन चलाओ'). It finds "
        "the best matching video and opens it so it starts playing. Do not use open_url or "
        "web_search for this."
    )
    input_model = PlayYouTubeInput

    def __init__(self, finder: VideoFinder, browser: BrowserOpener, *, timeout_s: float) -> None:
        self._finder = finder
        self._browser = browser
        self.timeout_s = timeout_s

    async def run(self, args: PlayYouTubeInput, ctx: ToolContext) -> ToolOutcome:
        try:
            match = first_video(await self._finder.find_videos(args.query))
        except SearchError:
            match = None
        if match is None:
            return await self._open_results(args.query)
        video_id, title = match
        url = WATCH_URL.format(video_id=video_id)
        try:
            await asyncio.to_thread(self._browser.open, url)
        except LaunchError as exc:
            return ToolOutcome(content=f"Could not open YouTube: {exc}", ok=False, summary="Failed")
        await ctx.bus.publish(Sources(items=(SourceItem(title=title, url=url),)))
        return ToolOutcome(
            # Only data here: the whole result is wrapped as untrusted because of the title.
            content=f"Opened and playing in the browser. Video title: {title}",
            ok=True,
            summary=f"Playing on YouTube: {title}",
            untrusted=True,  # the title comes from the web
        )

    async def _open_results(self, query: str) -> ToolOutcome:
        url = RESULTS_URL.format(query=quote_plus(query))
        try:
            await asyncio.to_thread(self._browser.open, url)
        except LaunchError as exc:
            return ToolOutcome(content=f"Could not open YouTube: {exc}", ok=False, summary="Failed")
        return ToolOutcome(
            content=(
                "No single video could be identified, so YouTube's search results for the request "
                "are open instead. Nothing is playing yet: tell the user to pick one."
            ),
            ok=True,
            summary="Opened YouTube search results",
        )
