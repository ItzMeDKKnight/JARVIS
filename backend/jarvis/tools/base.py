"""The tool interface every capability implements.

A tool is a small class with a pydantic input model, a description written
for the language model, and an async ``run``. To add a capability later (smart
home, calendar, ...), drop in a new module with one ``Tool`` subclass and
register it in ``jarvis.tools.registry.build_default_registry``. See
docs/adding-a-tool.md.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, Generic, Protocol, TypeVar

from pydantic import BaseModel, ConfigDict

from jarvis.events import EventBus
from jarvis.utils.language import Language


class Confirmer(Protocol):
    """Asks the user a yes/no question (typed now, spoken from Phase 3)."""

    async def confirm(self, question: str) -> bool:
        """Return True only on an explicit yes."""
        ...


class ToolInput(BaseModel):
    """Base for tool inputs: unknown fields are rejected.

    That gives the schema sent to Claude ``additionalProperties: false`` and
    makes typos in tool arguments fail validation instead of being ignored.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


@dataclass(frozen=True)
class ToolContext:
    """What a running tool may use besides its input."""

    bus: EventBus
    confirmer: Confirmer
    language: Language
    # What the user said or typed this turn, verbatim (the screen agent acts only on this).
    user_text: str = ""


@dataclass(frozen=True)
class ToolOutcome:
    """Result of a tool call.

    ``content`` goes back to Claude as the tool_result. ``summary`` is a short
    human-readable line for the UI and the log. ``untrusted`` marks content
    that came from outside (web pages), which the registry wraps in
    <untrusted_tool_output> tags so the persona treats it as data only.
    """

    content: str
    ok: bool
    summary: str
    untrusted: bool = False


InputT = TypeVar("InputT", bound=ToolInput)


class Tool(ABC, Generic[InputT]):
    """Base class for tools. Subclasses set the class attributes and implement run()."""

    name: ClassVar[str]
    description: ClassVar[str]
    input_model: type[InputT]
    # None means the tool manages its own timeouts (e.g. it waits for a confirmation).
    timeout_s: float | None = None

    def narration_target(self, args: InputT) -> str | None:
        """Value for {target} in the narration line, or None to skip narration entirely."""
        return ""

    @abstractmethod
    async def run(self, args: InputT, ctx: ToolContext) -> ToolOutcome:
        """Perform the action. Raise nothing for expected failures; return ok=False instead."""
