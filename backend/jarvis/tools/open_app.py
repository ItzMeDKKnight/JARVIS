"""open_app: launch an allowlisted application, or a confirmed Start Menu match."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from pydantic import Field

from jarvis.config import LocalizedText
from jarvis.os_adapters.base import AppLauncher, BrowserOpener, LaunchError
from jarvis.tools.app_catalog import AppCatalog
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome
from jarvis.tools.site_catalog import SiteCatalog

logger = logging.getLogger(__name__)


class OpenAppInput(ToolInput):
    app_name: str = Field(
        min_length=1,
        max_length=100,
        description="The application name exactly as the user said it, in any language.",
    )


class OpenAppTool(Tool[OpenAppInput]):
    """Opens apps from apps.yaml; anything else needs a Start Menu match plus a "yes".

    "Open GitHub" often arrives here rather than at open_url, so a name that is not an
    app but is a website listed in apps.yaml opens that site in the browser instead.
    """

    name = "open_app"
    description = (
        "Open a desktop application on the user's computer. Call this when the user asks to "
        "open, launch or start an app (for example 'open VS Code', 'calculator kholo'). "
        "Never use it for websites; use open_url for those."
    )
    input_model = OpenAppInput
    timeout_s = None  # waits for a confirmation; launch and confirm have their own timeouts

    def __init__(
        self,
        catalog: AppCatalog,
        launcher: AppLauncher,
        *,
        launch_timeout_s: float,
        confirm_timeout_s: float,
        search_start_menu: bool,
        confirm_question: LocalizedText,
        sites: SiteCatalog | None = None,
        browser: BrowserOpener | None = None,
    ) -> None:
        self._catalog = catalog
        self._launcher = launcher
        self._launch_timeout_s = launch_timeout_s
        self._confirm_timeout_s = confirm_timeout_s
        self._search_start_menu = search_start_menu
        self._confirm_question = confirm_question
        self._sites = sites if browser is not None else None
        self._browser = browser

    def narration_target(self, args: OpenAppInput) -> str | None:
        app = self._catalog.resolve(args.app_name)
        if app is not None:
            return app.display_name
        site = self._sites.resolve(args.app_name) if self._sites is not None else None
        # Unknown apps get a confirmation question instead of "Opening ...".
        return site.display_name if site is not None else None

    async def run(self, args: OpenAppInput, ctx: ToolContext) -> ToolOutcome:
        app = self._catalog.resolve(args.app_name)
        if app is not None:
            return await self._launch_known(
                app.display_name, lambda: self._launcher.launch(app.launch)
            )
        site = self._sites.resolve(args.app_name) if self._sites is not None else None
        if site is not None and self._browser is not None:
            browser = self._browser
            return await self._launch_known(site.display_name, lambda: browser.open(site.url))
        if not self._search_start_menu:
            return _not_found(args.app_name)
        return await self._launch_unknown(args.app_name, ctx)

    async def _launch_known(self, display_name: str, launch: Callable[[], None]) -> ToolOutcome:
        try:
            await asyncio.wait_for(asyncio.to_thread(launch), self._launch_timeout_s)
        except LaunchError as exc:
            return ToolOutcome(
                content=f"Could not open {display_name}: {exc}.",
                ok=False,
                summary=f"Failed to open {display_name}",
            )
        except TimeoutError:
            return ToolOutcome(
                content=f"Opening {display_name} timed out.",
                ok=False,
                summary=f"Timed out opening {display_name}",
            )
        return ToolOutcome(
            content=f"Opened {display_name}.", ok=True, summary=f"Opened {display_name}"
        )

    async def _launch_unknown(self, requested: str, ctx: ToolContext) -> ToolOutcome:
        matches = await asyncio.to_thread(self._launcher.find_shortcuts, requested, 1)
        if not matches:
            return _not_found(requested)
        shortcut = matches[0]
        question = (
            self._confirm_question.for_language(ctx.language)
            .replace("{target}", requested)
            .replace("{match}", shortcut.name)
        )
        try:
            approved = await asyncio.wait_for(
                ctx.confirmer.confirm(question), self._confirm_timeout_s
            )
        except TimeoutError:
            approved = False
        if not approved:
            logger.info("unknown app launch declined", extra={"shortcut": shortcut.name})
            return ToolOutcome(
                content=f"The user chose not to open {shortcut.name}. Nothing was opened.",
                ok=True,
                summary="Launch declined",
            )
        return await self._launch_known(
            shortcut.name, lambda: self._launcher.launch_shortcut(shortcut)
        )


def _not_found(requested: str) -> ToolOutcome:
    return ToolOutcome(
        content=(
            f"'{requested}' is not in the app allowlist and no matching Start Menu shortcut was "
            "found. Nothing was opened. Tell the user, and mention it can be added to "
            "config/apps.yaml."
        ),
        ok=False,
        summary=f"Unknown app: {requested}",
    )
