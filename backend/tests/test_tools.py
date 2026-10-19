from __future__ import annotations

from jarvis.config import JarvisConfig
from jarvis.events import CodeBlock, EventBus, Sources
from jarvis.search.base import SearchResult, SearchSource
from jarvis.tools.app_catalog import AppCatalog
from jarvis.tools.base import ToolContext
from jarvis.tools.open_app import OpenAppInput, OpenAppTool
from jarvis.tools.show_code import ShowCodeInput, ShowCodeTool
from jarvis.tools.web_search import WebSearchInput, WebSearchTool
from tests.fakes import EventRecorder, FakeConfirmer, FakeLauncher, FakeSearch, shortcut


def _open_app(config: JarvisConfig, launcher: FakeLauncher, *, search: bool = True) -> OpenAppTool:
    return OpenAppTool(
        AppCatalog(config.apps),
        launcher,
        launch_timeout_s=5,
        confirm_timeout_s=5,
        search_start_menu=search,
        confirm_question=config.messages.confirm_unknown_app,
    )


def _ctx(confirmer: FakeConfirmer, bus: EventBus | None = None) -> ToolContext:
    return ToolContext(bus=bus or EventBus(), confirmer=confirmer, language="en")


async def test_allowlisted_app_launches_without_confirmation(config: JarvisConfig) -> None:
    launcher = FakeLauncher()
    confirmer = FakeConfirmer(False)
    outcome = await _open_app(config, launcher).run(
        OpenAppInput(app_name="VS Code"), _ctx(confirmer)
    )

    assert outcome.ok
    assert [s.target for s in launcher.launched] == ["code.exe"]
    assert confirmer.questions == []


async def test_launch_failure_is_reported(config: JarvisConfig) -> None:
    launcher = FakeLauncher(fail="that app doesn't seem to be installed")
    outcome = await _open_app(config, launcher).run(
        OpenAppInput(app_name="chrome"), _ctx(FakeConfirmer(True))
    )
    assert not outcome.ok
    assert "not" in outcome.content and "installed" in outcome.content


async def test_unknown_app_requires_confirmation(config: JarvisConfig) -> None:
    launcher = FakeLauncher(shortcuts=[shortcut("Spotify")])
    confirmer = FakeConfirmer(True)

    outcome = await _open_app(config, launcher).run(
        OpenAppInput(app_name="spotify"), _ctx(confirmer)
    )

    assert outcome.ok
    assert len(confirmer.questions) == 1 and "Spotify" in confirmer.questions[0]
    assert [s.name for s in launcher.launched_shortcuts] == ["Spotify"]


async def test_declined_unknown_app_is_not_launched(config: JarvisConfig) -> None:
    launcher = FakeLauncher(shortcuts=[shortcut("Spotify")])
    outcome = await _open_app(config, launcher).run(
        OpenAppInput(app_name="spotify"), _ctx(FakeConfirmer(False))
    )
    assert launcher.launched_shortcuts == []
    assert "Nothing was opened" in outcome.content


async def test_unknown_app_without_match_launches_nothing(config: JarvisConfig) -> None:
    launcher = FakeLauncher(shortcuts=[])
    confirmer = FakeConfirmer(True)
    outcome = await _open_app(config, launcher).run(
        OpenAppInput(app_name="photoshop"), _ctx(confirmer)
    )
    assert not outcome.ok
    assert confirmer.questions == []
    assert launcher.launched == [] and launcher.launched_shortcuts == []


async def test_start_menu_search_can_be_disabled(config: JarvisConfig) -> None:
    launcher = FakeLauncher(shortcuts=[shortcut("Spotify")])
    confirmer = FakeConfirmer(True)
    tool = _open_app(config, launcher, search=False)
    outcome = await tool.run(OpenAppInput(app_name="spotify"), _ctx(confirmer))
    assert not outcome.ok and confirmer.questions == []


async def test_show_code_publishes_code_block() -> None:
    bus = EventBus()
    recorder = EventRecorder()
    bus.subscribe(recorder)
    args = ShowCodeInput(title="Binary search", language="python", code="def f(): ...")

    outcome = await ShowCodeTool(timeout_s=5).run(args, _ctx(FakeConfirmer(True), bus))

    assert outcome.ok
    [block] = recorder.of_type("code_block")
    assert isinstance(block, CodeBlock) and block.code == "def f(): ..."


async def test_web_search_publishes_sources() -> None:
    bus = EventBus()
    recorder = EventRecorder()
    bus.subscribe(recorder)
    result = SearchResult(
        summary="It is 31 degrees and sunny in Mumbai.",
        sources=(SearchSource(title="Weather", url="https://weather.example/mumbai"),),
    )
    tool = WebSearchTool(FakeSearch(result), timeout_s=5)

    outcome = await tool.run(WebSearchInput(query="weather Mumbai"), _ctx(FakeConfirmer(True), bus))

    assert outcome.ok and outcome.untrusted
    [sources] = recorder.of_type("sources")
    assert isinstance(sources, Sources)
    assert sources.items[0].url == "https://weather.example/mumbai"


async def test_web_search_failure_is_an_error_result() -> None:
    tool = WebSearchTool(FakeSearch(error="the search service could not be reached"), timeout_s=5)
    outcome = await tool.run(WebSearchInput(query="x"), _ctx(FakeConfirmer(True)))
    assert not outcome.ok and "could not be reached" in outcome.content
