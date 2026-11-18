"""open_url: open an http(s) address in the default browser."""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit

from pydantic import Field

from jarvis.os_adapters.base import BrowserOpener, LaunchError
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome
from jarvis.tools.site_catalog import SiteCatalog

ALLOWED_SCHEMES = frozenset({"http", "https"})
MAX_URL_LENGTH = 2048


class InvalidUrlError(ValueError):
    """The URL is not something Jarvis is willing to open."""


def validate_url(raw: str) -> str:
    """Return a safe, normalised http(s) URL or raise InvalidUrlError.

    Rules, and why:
    * Only http and https. ``file:``, ``javascript:``, ``ms-settings:`` and
      other schemes can run code or reach the local system through the
      browser or the OS URI handlers.
    * A bare domain ("youtube.com") gets ``https://`` added, because that is
      what people say out loud.
    * No user:password@ part, which is a classic way to disguise the real host.
    * No whitespace or control characters, and a sane length limit.
    """
    url = raw.strip()
    if not url or len(url) > MAX_URL_LENGTH:
        raise InvalidUrlError("the address is empty or too long")
    if any(char.isspace() or ord(char) < 0x20 for char in url):
        raise InvalidUrlError("the address contains spaces or control characters")

    parts = urlsplit(url)
    # "example.com:8080" parses as scheme "example.com" with path "8080": also a bare domain.
    if not parts.scheme or (not parts.netloc and parts.path.isdigit()):
        parts = urlsplit(f"https://{url}")

    if parts.scheme.lower() not in ALLOWED_SCHEMES:
        raise InvalidUrlError(f"'{parts.scheme}' links are not allowed, only http and https")
    if "@" in parts.netloc:
        raise InvalidUrlError("addresses with a username or password are not allowed")
    if not parts.hostname:
        raise InvalidUrlError("the address has no host name")
    return parts._replace(scheme=parts.scheme.lower()).geturl()


class OpenUrlInput(ToolInput):
    url: str = Field(
        min_length=1,
        max_length=MAX_URL_LENGTH,
        description=(
            "Full http or https address (e.g. https://www.youtube.com), or the name of a "
            "well-known site exactly as the user said it (e.g. 'GitHub', 'Gmail', 'जीमेल')."
        ),
    )


class OpenUrlTool(Tool[OpenUrlInput]):
    name = "open_url"
    description = (
        "Open a website in the user's default browser. Call this when the user asks to open or "
        "go to a site or page, by address or by name ('open GitHub', 'Gmail kholo'). Only http "
        "and https addresses are accepted."
    )
    input_model = OpenUrlInput

    def __init__(
        self, browser: BrowserOpener, *, timeout_s: float, sites: SiteCatalog | None = None
    ) -> None:
        self._browser = browser
        self._sites = sites
        self.timeout_s = timeout_s

    def _resolve(self, raw: str) -> str:
        """A known site name becomes its allowlisted address; anything else must be a URL."""
        site = self._sites.resolve(raw) if self._sites is not None else None
        if site is not None:
            return validate_url(site.url)
        url = validate_url(raw)
        host = urlsplit(url).hostname or ""
        if "." not in host and host != "localhost":
            # "open github" without a match would become https://github, which goes nowhere.
            raise InvalidUrlError(f"'{raw}' is not a known site name; give the full address")
        return url

    def narration_target(self, args: OpenUrlInput) -> str | None:
        try:
            return urlsplit(self._resolve(args.url)).hostname or ""
        except InvalidUrlError:
            return None  # nothing will be opened, so do not announce it

    async def run(self, args: OpenUrlInput, ctx: ToolContext) -> ToolOutcome:
        try:
            url = self._resolve(args.url)
        except InvalidUrlError as exc:
            return ToolOutcome(
                content=f"Refused to open that address: {exc}.", ok=False, summary="URL rejected"
            )
        try:
            await asyncio.to_thread(self._browser.open, url)
        except LaunchError as exc:
            return ToolOutcome(
                content=f"Could not open the page: {exc}", ok=False, summary="Failed"
            )
        host = urlsplit(url).hostname
        return ToolOutcome(
            content=f"Opened {url} in the browser.", ok=True, summary=f"Opened {host}"
        )
