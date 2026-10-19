from __future__ import annotations

import pytest

from jarvis.events import EventBus
from jarvis.tools.base import ToolContext
from jarvis.tools.open_url import InvalidUrlError, OpenUrlInput, OpenUrlTool, validate_url
from tests.fakes import FakeBrowser, FakeConfirmer


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://www.youtube.com", "https://www.youtube.com"),
        ("http://example.com/path?q=1", "http://example.com/path?q=1"),
        ("HTTPS://Example.com", "https://Example.com"),
        ("youtube.com", "https://youtube.com"),
        ("github.com/anthropics", "https://github.com/anthropics"),
        ("localhost:3000", "https://localhost:3000"),
        ("  https://python.org  ", "https://python.org"),
    ],
)
def test_accepts_http_and_https(raw: str, expected: str) -> None:
    assert validate_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "file:///C:/Windows/System32/cmd.exe",
        "ftp://example.com",
        "ms-settings:privacy",
        "data:text/html,<script>alert(1)</script>",
        "vbscript:msgbox",
        "https://user:pass@evil.example",
        "https://",
        "//evil.example",
        "https://exa mple.com",
        "https://example.com/\x00",
        "",
        "https://" + "a" * 3000 + ".com",
    ],
)
def test_rejects_everything_else(raw: str) -> None:
    with pytest.raises(InvalidUrlError):
        validate_url(raw)


async def test_tool_opens_valid_url_and_refuses_invalid() -> None:
    browser = FakeBrowser()
    tool = OpenUrlTool(browser, timeout_s=5)
    ctx = ToolContext(bus=EventBus(), confirmer=FakeConfirmer(False), language="en")

    ok = await tool.run(OpenUrlInput(url="youtube.com"), ctx)
    bad = await tool.run(OpenUrlInput(url="file:///etc/passwd"), ctx)

    assert ok.ok and browser.opened == ["https://youtube.com"]
    assert not bad.ok and len(browser.opened) == 1


def test_no_narration_for_invalid_url() -> None:
    tool = OpenUrlTool(FakeBrowser(), timeout_s=5)
    assert tool.narration_target(OpenUrlInput(url="javascript:alert(1)")) is None
    assert tool.narration_target(OpenUrlInput(url="https://python.org")) == "python.org"
