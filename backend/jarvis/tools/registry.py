"""Tool registration, JSON schemas for Claude, and safe execution.

The registry is the only place tools are invoked. Centralising execution means
every tool, present and future, gets the same guarantees for free:

* input validated against the tool's pydantic model before anything runs
  (needed because eager input streaming skips server-side validation);
* the narration line published *before* the tool starts, so Jarvis is never
  silent during a slow action;
* a timeout, and exceptions turned into an error result instead of a crash;
* untrusted output (web content) wrapped so the persona treats it as data.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from jarvis.config import JarvisConfig, LocalizedText
from jarvis.events import ToolFinished, ToolStarted
from jarvis.memory.manager import MemoryService
from jarvis.os_adapters.base import AppLauncher, BrowserOpener
from jarvis.search.base import SearchProvider
from jarvis.tools.app_catalog import AppCatalog
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome
from jarvis.tools.memory_tools import ForgetMemoryTool, RecallMemoryTool, RememberTool
from jarvis.tools.open_app import OpenAppTool
from jarvis.tools.open_url import OpenUrlTool
from jarvis.tools.show_code import ShowCodeTool
from jarvis.tools.web_search import WebSearchTool

logger = logging.getLogger(__name__)

UNTRUSTED_OPEN = '<untrusted_tool_output source="{name}">'
UNTRUSTED_CLOSE = "</untrusted_tool_output>"


class ToolRegistry:
    """Holds the available tools and runs them on Claude's behalf."""

    def __init__(self, narration: dict[str, LocalizedText]) -> None:
        self._tools: dict[str, Tool[Any]] = {}
        self._narration = narration

    def register(self, tool: Tool[Any]) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    @property
    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self) -> list[dict[str, Any]]:
        """Tool definitions for the Messages API, in a stable order.

        Sorting by name keeps the request prefix byte-identical between turns,
        which prompt caching depends on.
        """
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_model.model_json_schema(),
                # Stream tool inputs (notably long show_code bodies) as they are generated.
                "eager_input_streaming": True,
            }
            for _, tool in sorted(self._tools.items())
        ]

    async def execute(
        self, call_id: str, name: str, raw_input: object, ctx: ToolContext
    ) -> ToolOutcome:
        """Validate, narrate, run and report one tool call. Never raises for tool failures."""
        tool = self._tools.get(name)
        if tool is None:
            return ToolOutcome(content=f"Unknown tool '{name}'.", ok=False, summary="Unknown tool")
        try:
            args = tool.input_model.model_validate(raw_input)
        except ValidationError as exc:
            logger.warning("invalid tool input", extra={"tool": name, "errors": exc.error_count()})
            return ToolOutcome(
                content=f"INVALID_INPUT for {name}: {exc.errors(include_input=False)}",
                ok=False,
                summary="Invalid tool input",
            )

        narration = self._narration_for(tool, args, ctx)
        await ctx.bus.publish(ToolStarted(call_id=call_id, name=name, summary=narration))
        outcome = await self._run_guarded(tool, args, ctx)
        await ctx.bus.publish(
            ToolFinished(call_id=call_id, name=name, ok=outcome.ok, summary=outcome.summary)
        )
        logger.info("tool finished", extra={"tool": name, "ok": outcome.ok})
        return _wrap_untrusted(name, outcome)

    def _narration_for(self, tool: Tool[Any], args: ToolInput, ctx: ToolContext) -> str:
        template = self._narration.get(tool.name)
        target = tool.narration_target(args)
        if template is None or target is None:
            return ""
        return template.for_language(ctx.language).replace("{target}", target)

    async def _run_guarded(self, tool: Tool[Any], args: ToolInput, ctx: ToolContext) -> ToolOutcome:
        try:
            if tool.timeout_s is None:
                return await tool.run(args, ctx)
            return await asyncio.wait_for(tool.run(args, ctx), tool.timeout_s)
        except TimeoutError:
            logger.warning("tool timed out", extra={"tool": tool.name, "timeout_s": tool.timeout_s})
            return ToolOutcome(
                content=(
                    f"{tool.name} timed out after {tool.timeout_s:g} seconds and did not finish. "
                    "Tell the user plainly and suggest trying again."
                ),
                ok=False,
                summary="Timed out",
            )
        except Exception:
            logger.exception("tool crashed", extra={"tool": tool.name})
            return ToolOutcome(
                content=(
                    f"{tool.name} failed with an internal error. Tell the user it didn't work; "
                    "do not claim it succeeded."
                ),
                ok=False,
                summary="Crashed",
            )


def _wrap_untrusted(name: str, outcome: ToolOutcome) -> ToolOutcome:
    if not outcome.untrusted:
        return outcome
    # Neutralise a closing tag inside the content so a web page cannot "escape" the wrapper.
    body = outcome.content.replace(UNTRUSTED_CLOSE, "</untrusted_tool_output_>")
    wrapped = f"{UNTRUSTED_OPEN.format(name=name)}\n{body}\n{UNTRUSTED_CLOSE}"
    return ToolOutcome(content=wrapped, ok=outcome.ok, summary=outcome.summary, untrusted=True)


def build_default_registry(
    config: JarvisConfig,
    *,
    launcher: AppLauncher,
    browser: BrowserOpener,
    search: SearchProvider,
    memory: MemoryService | None = None,
) -> ToolRegistry:
    """Register the built-in tools (memory tools only when memory is enabled)."""
    tools_cfg = config.tools
    registry = ToolRegistry(config.narration)
    registry.register(
        OpenAppTool(
            AppCatalog(config.apps),
            launcher,
            launch_timeout_s=tools_cfg.timeout_s,
            confirm_timeout_s=tools_cfg.confirm_timeout_s,
            search_start_menu=tools_cfg.search_start_menu_for_unknown_apps,
            confirm_question=config.messages.confirm_unknown_app,
        )
    )
    registry.register(OpenUrlTool(browser, timeout_s=tools_cfg.timeout_s))
    registry.register(WebSearchTool(search, timeout_s=config.search.timeout_s))
    registry.register(ShowCodeTool(timeout_s=tools_cfg.timeout_s))
    if memory is not None:
        registry.register(RememberTool(memory, timeout_s=tools_cfg.timeout_s))
        registry.register(RecallMemoryTool(memory, timeout_s=tools_cfg.timeout_s))
        registry.register(
            ForgetMemoryTool(
                memory,
                confirm_timeout_s=tools_cfg.confirm_timeout_s,
                confirm_matches=config.messages.confirm_forget_matches,
                confirm_all=config.messages.confirm_forget_all,
            )
        )
    return registry
