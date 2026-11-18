"""play_youtube: pick a real video from untrusted search results, open only a rebuilt URL."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.config import JarvisConfig
from jarvis.events import EventBus
from jarvis.search.anthropic_search import parse_found_pages
from jarvis.search.base import SearchError, SearchSource
from jarvis.tools.base import ToolContext
from jarvis.tools.play_youtube import PlayYouTubeTool, first_video, video_id_from_url
from jarvis.tools.registry import build_default_registry
from tests.fakes import (
    EventRecorder,
    FakeBrowser,
    FakeConfirmer,
    FakeLauncher,
    FakeSearch,
    FakeVideoFinder,
)

VIDEO = "dQw4w9WgXcQ"


@pytest.mark.parametrize(
    "url",
    [
        f"https://www.youtube.com/watch?v={VIDEO}",
        f"https://m.youtube.com/watch?v={VIDEO}&t=42s",
        f"https://youtube.com/watch?feature=share&v={VIDEO}",
        f"https://youtu.be/{VIDEO}?si=abc",
        f"https://www.youtube.com/shorts/{VIDEO}",
        f"https://music.youtube.com/watch?v={VIDEO}",
    ],
)
def test_video_ids_are_found_in_every_youtube_url_shape(url: str) -> None:
    assert video_id_from_url(url) == VIDEO


@pytest.mark.parametrize(
    "url",
    [
        "https://www.youtube.com/@SomeChannel",
        "https://www.youtube.com/playlist?list=PL123",
        "https://www.youtube.com/results?search_query=x",
        f"https://evil.example.com/watch?v={VIDEO}",  # not YouTube
        f"https://youtube.com.evil.example/watch?v={VIDEO}",
        f"javascript:alert(1)//youtube.com/watch?v={VIDEO}",
        "https://www.youtube.com/watch?v=short",
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=<script>",  # first value used, valid
        "https://www.youtube.com/watch?v=dQw4w9WgXc%22",
        "not a url at all",
    ],
)
def test_non_videos_and_foreign_hosts_are_rejected(url: str) -> None:
    result = video_id_from_url(url)
    assert result is None or result == VIDEO
    if "evil" in url or url.startswith("javascript"):
        assert result is None


def test_first_video_skips_channels_and_playlists() -> None:
    sources = (
        SearchSource(title="Channel", url="https://www.youtube.com/@Artist"),
        SearchSource(title="Song (Official Video)", url=f"https://www.youtube.com/watch?v={VIDEO}"),
    )
    assert first_video(sources) == (VIDEO, "Song (Official Video)")
    assert first_video(()) is None


def _ctx() -> tuple[ToolContext, EventRecorder]:
    bus, events = EventBus(), EventRecorder()
    bus.subscribe(events)
    return ToolContext(bus=bus, confirmer=FakeConfirmer(False), language="en"), events


async def test_plays_the_best_video_with_a_rebuilt_url() -> None:
    finder = FakeVideoFinder(
        (
            SearchSource(
                title="Kesariya (Official Video)",
                url=f"https://youtu.be/{VIDEO}?si=tracking&next=https://evil.example",
            ),
        )
    )
    browser = FakeBrowser()
    ctx, events = _ctx()
    outcome = await PlayYouTubeTool(finder, browser, timeout_s=5).run(
        PlayYouTubeTool.input_model(query="Kesariya Arijit Singh"), ctx
    )
    assert outcome.ok and outcome.untrusted  # the title came from the web
    assert browser.opened == [f"https://www.youtube.com/watch?v={VIDEO}"]  # nothing else kept
    assert finder.queries == ["Kesariya Arijit Singh"]
    assert "Kesariya (Official Video)" in outcome.content
    assert events.of_type("sources")[0].items[0].url == browser.opened[0]


@pytest.mark.parametrize("finder", [FakeVideoFinder(()), FakeVideoFinder(error="no results")])
async def test_falls_back_to_youtube_search_results(finder: FakeVideoFinder) -> None:
    browser = FakeBrowser()
    ctx, _ = _ctx()
    outcome = await PlayYouTubeTool(finder, browser, timeout_s=5).run(
        PlayYouTubeTool.input_model(query="भजन & aarti"), ctx
    )
    assert outcome.ok
    assert browser.opened == [
        "https://www.youtube.com/results?search_query=%E0%A4%AD%E0%A4%9C%E0%A4%A8+%26+aarti"
    ]
    assert "Nothing is playing yet" in outcome.content


def _block(kind: str, **fields: object) -> SimpleNamespace:
    return SimpleNamespace(type=kind, **fields)


def test_found_pages_put_cited_results_first_and_dedupe() -> None:
    cited = SimpleNamespace(
        type="web_search_result_location", title="Cited", url="https://youtu.be/AAAAAAAAAAA"
    )
    content = [
        _block(
            "web_search_tool_result",
            content=[
                SimpleNamespace(title="Other", url="https://www.youtube.com/watch?v=BBBBBBBBBBB"),
                SimpleNamespace(title="Cited", url="https://youtu.be/AAAAAAAAAAA"),
            ],
        ),
        _block("text", text="Cited", citations=[cited]),
    ]
    pages = parse_found_pages(content)
    assert [p.title for p in pages] == ["Cited", "Other"]


def test_found_pages_report_search_errors() -> None:
    content = [_block("web_search_tool_result", content=SimpleNamespace(error_code="unavailable"))]
    with pytest.raises(SearchError):
        parse_found_pages(content)


def test_registered_only_with_a_video_finder(config: JarvisConfig) -> None:
    def names(**extra: object) -> list[str]:
        return build_default_registry(
            config,
            launcher=FakeLauncher(),
            browser=FakeBrowser(),
            search=FakeSearch(),
            **extra,  # type: ignore[arg-type]
        ).names

    assert "play_youtube" not in names()
    assert "play_youtube" in names(videos=FakeVideoFinder())
    assert "play_youtube" in config.narration


def test_video_search_is_restricted_to_youtube(config: JarvisConfig) -> None:
    from jarvis.search.anthropic_search import VIDEO_DOMAINS, AnthropicWebSearch

    search = AnthropicWebSearch(None, config.search)  # type: ignore[arg-type]
    video_tool = search._tool_definition(max_uses=1, allowed_domains=VIDEO_DOMAINS)
    assert video_tool["allowed_domains"] == ["youtube.com"]  # type: ignore[typeddict-item]
    assert video_tool["max_uses"] == 1  # type: ignore[typeddict-item]
    assert "allowed_domains" not in search._tool_definition()  # normal search is unrestricted
