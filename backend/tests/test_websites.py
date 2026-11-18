"""Opening websites by spoken name ("open GitHub", "जीमेल खोलो"), with the http(s)-only rule."""

from __future__ import annotations

import pytest

from jarvis.config import AppEntry, JarvisConfig, LaunchSpec, WebsiteEntry
from jarvis.events import EventBus
from jarvis.tools.app_catalog import AppCatalog
from jarvis.tools.base import ToolContext
from jarvis.tools.open_app import OpenAppTool
from jarvis.tools.open_url import OpenUrlInput, OpenUrlTool
from jarvis.tools.site_catalog import DuplicateSiteAliasError, SiteCatalog
from tests.fakes import FakeBrowser, FakeConfirmer, FakeLauncher, shortcut


def _ctx() -> ToolContext:
    return ToolContext(bus=EventBus(), confirmer=FakeConfirmer(True), language="en")


@pytest.mark.parametrize(
    ("spoken", "url"),
    [
        ("GitHub", "https://github.com"),
        ("open the github website", "https://github.com"),
        ("Gmail", "https://mail.google.com"),
        ("जीमेल खोलो", "https://mail.google.com"),
        ("youtube kholo", "https://www.youtube.com"),
        ("यूट्यूब", "https://www.youtube.com"),
        ("Stack-Overflow", "https://stackoverflow.com"),
    ],
)
def test_spoken_site_names_resolve(config: JarvisConfig, spoken: str, url: str) -> None:
    site = SiteCatalog(config.websites).resolve(spoken)
    assert site is not None and site.url == url


def test_shipped_websites_are_all_https(config: JarvisConfig) -> None:
    assert config.websites
    assert all(site.url.startswith("https://") for site in config.websites)


def test_duplicate_site_aliases_are_rejected() -> None:
    sites = [
        WebsiteEntry(id="a", display_name="A", aliases=("mail",), url="https://a.example"),
        WebsiteEntry(id="b", display_name="B", aliases=("mail",), url="https://b.example"),
    ]
    with pytest.raises(DuplicateSiteAliasError):
        SiteCatalog(sites)


def test_website_urls_must_be_http_or_https() -> None:
    with pytest.raises(ValueError, match="url"):
        WebsiteEntry(id="x", display_name="X", aliases=("x",), url="file:///C:/Windows")


async def test_open_url_accepts_site_names(config: JarvisConfig) -> None:
    browser = FakeBrowser()
    tool = OpenUrlTool(browser, timeout_s=5, sites=SiteCatalog(config.websites))
    for name in ("GitHub", "gmail", "https://example.com/page", "wikipedia.org"):
        outcome = await tool.run(OpenUrlInput(url=name), _ctx())
        assert outcome.ok, name
    assert browser.opened == [
        "https://github.com",
        "https://mail.google.com",
        "https://example.com/page",
        "https://wikipedia.org",
    ]


@pytest.mark.parametrize(
    "bad", ["notasite", "javascript:alert(1)", "file:///C:/Windows/win.ini", "ms-settings:"]
)
async def test_open_url_still_refuses_non_http_and_unknown_names(
    config: JarvisConfig, bad: str
) -> None:
    browser = FakeBrowser()
    tool = OpenUrlTool(browser, timeout_s=5, sites=SiteCatalog(config.websites))
    outcome = await tool.run(OpenUrlInput(url=bad), _ctx())
    assert not outcome.ok and browser.opened == []
    assert tool.narration_target(OpenUrlInput(url=bad)) is None  # nothing announced either


def _open_app(config: JarvisConfig, launcher: FakeLauncher, browser: FakeBrowser) -> OpenAppTool:
    apps = (
        AppEntry(
            id="calculator",
            display_name="Calculator",
            aliases=("calculator",),
            launch=LaunchSpec(kind="appx", target="Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"),
        ),
    )
    return OpenAppTool(
        AppCatalog(apps),
        launcher,
        launch_timeout_s=5,
        confirm_timeout_s=5,
        search_start_menu=True,
        confirm_question=config.messages.confirm_unknown_app,
        sites=SiteCatalog(config.websites),
        browser=browser,
    )


async def test_open_app_falls_back_to_a_known_website(config: JarvisConfig) -> None:
    """'Open GitHub' often reaches open_app; it opens the site, without asking."""
    launcher = FakeLauncher(shortcuts=[shortcut("GitHub Desktop")])
    browser = FakeBrowser()
    tool = _open_app(config, launcher, browser)
    args = tool.input_model(app_name="GitHub")
    assert tool.narration_target(args) == "GitHub"
    outcome = await tool.run(args, _ctx())
    assert outcome.ok and browser.opened == ["https://github.com"]
    assert launcher.launched_shortcuts == []


async def test_apps_still_win_over_websites(config: JarvisConfig) -> None:
    launcher, browser = FakeLauncher(), FakeBrowser()
    tool = _open_app(config, launcher, browser)
    outcome = await tool.run(tool.input_model(app_name="calculator"), _ctx())
    assert outcome.ok and len(launcher.launched) == 1 and browser.opened == []
