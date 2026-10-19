"""Phase 1 terminal front-end: typed input, printed replies.

It is deliberately "dumb": it renders events from the bus exactly as the HUD
will later, so the conversation core does not know or care which front-end
is attached.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from collections.abc import Awaitable, Callable
from typing import TextIO

from pydantic import BaseModel

from jarvis.confirm import is_yes
from jarvis.events import CodeBlock, ErrorEvent, Sources, ToolFinished, ToolStarted, Transcript

_QUIT_COMMANDS = frozenset({"/quit", "/exit"})
PROMPT = "You: "
SPEAKER = "Jarvis: "


class TerminalRenderer:
    """Event-bus subscriber that prints assistant output to a text stream."""

    def __init__(self, out: TextIO = sys.stdout) -> None:
        self._out = out
        self._printed: dict[str, int] = {}

    async def __call__(self, event: BaseModel) -> None:
        if isinstance(event, Transcript) and event.role == "assistant":
            self._render_transcript(event)
        elif isinstance(event, ToolStarted) and event.summary:
            self._line(f"{SPEAKER}{event.summary}")
        elif isinstance(event, ToolFinished):
            status = "ok" if event.ok else "failed"
            self._line(f"  [{event.name}: {status} - {event.summary}]")
        elif isinstance(event, CodeBlock):
            self._render_code(event)
        elif isinstance(event, Sources):
            self._line("  Sources:")
            for item in event.items:
                self._line(f"    - {item.title}\n      {item.url}")
        elif isinstance(event, ErrorEvent):
            self._line(f"  [error: {event.code}]")
        self._out.flush()

    def _render_transcript(self, event: Transcript) -> None:
        """Print only the new part of a streamed reply so tokens appear as they arrive."""
        already = self._printed.get(event.turn_id)
        if already is None:
            self._out.write(SPEAKER)
            already = 0
        self._out.write(event.text[already:])
        self._printed[event.turn_id] = len(event.text)
        if event.final:
            self._out.write("\n")
            del self._printed[event.turn_id]

    def _render_code(self, event: CodeBlock) -> None:
        rule = "-" * 72
        self._line(f"\n{rule}\n {event.title}  [{event.language}]\n{rule}")
        self._line(textwrap.dedent(event.code).rstrip())
        self._line(f"{rule}\n")

    def _line(self, text: str) -> None:
        self._out.write(text + "\n")


async def _read_line(prompt: str) -> str | None:
    """Read one line without blocking the event loop; None on end of input."""
    try:
        return await asyncio.to_thread(input, prompt)
    except EOFError:
        return None


class TerminalConfirmer:
    """Asks yes/no questions on the terminal. Anything but a clear yes counts as no."""

    async def confirm(self, question: str) -> bool:
        print(f"{SPEAKER}{question}", flush=True)
        answer = await _read_line("(yes/no) ")
        return is_yes(answer)


async def run_repl(handle_text: Callable[[str], Awaitable[None]]) -> None:
    """Prompt for input until /quit or end of input, passing each line to ``handle_text``."""
    print("J.A.R.V.I.S. text mode. Type a request, or /quit to leave.\n", flush=True)
    while True:
        line = await _read_line(PROMPT)
        if line is None or line.strip().casefold() in _QUIT_COMMANDS:
            return
        await handle_text(line)
