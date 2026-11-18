"""The event protocol between jarvis-core and its front-ends, plus the in-process event bus.

These pydantic models are the single source of truth for the protocol. The
JSON Schema in ``protocol/events.schema.json`` is generated from them
(``python scripts/export_protocol_schema.py``) and a test fails if the two
drift apart, so the Electron UI (Phase 5) can rely on that file.

Every message carries ``v`` (protocol version) and ``type`` (discriminator).
Bump :data:`PROTOCOL_VERSION` on any breaking change.

The terminal front-end consumes exactly the same events the HUD receives over
the WebSocket (jarvis.server); nothing prints directly from the core.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

PROTOCOL_VERSION: Literal[2] = 2

logger = logging.getLogger(__name__)


class AssistantState(StrEnum):
    """States of the assistant; see jarvis.state for the allowed transitions."""

    IDLE = "idle"
    LISTENING = "listening"
    THINKING = "thinking"
    SPEAKING = "speaking"
    ERROR = "error"
    MUTED = "muted"


class _Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    v: Literal[2] = PROTOCOL_VERSION


# --------------------------------------------------------------------------
# Backend -> UI events
# --------------------------------------------------------------------------


class StateChanged(_Message):
    type: Literal["state_changed"] = "state_changed"
    state: AssistantState


class AudioLevel(_Message):
    type: Literal["audio_level"] = "audio_level"
    source: Literal["mic", "tts"]
    level: float = Field(ge=0.0, le=1.0)


class Transcript(_Message):
    """One utterance. Partial updates repeat the same ``turn_id`` with the text so far."""

    type: Literal["transcript"] = "transcript"
    turn_id: str
    role: Literal["user", "assistant"]
    text: str
    language: Literal["en", "hi"]
    final: bool


class ToolStarted(_Message):
    """Emitted before a tool runs. ``summary`` is the narration line to speak."""

    type: Literal["tool_started"] = "tool_started"
    call_id: str
    name: str
    summary: str


class ToolFinished(_Message):
    type: Literal["tool_finished"] = "tool_finished"
    call_id: str
    name: str
    ok: bool
    summary: str


class CodeBlock(_Message):
    type: Literal["code_block"] = "code_block"
    title: str
    language: str
    code: str


class SourceItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    title: str
    url: str


class Sources(_Message):
    type: Literal["sources"] = "sources"
    items: tuple[SourceItem, ...]


class ErrorEvent(_Message):
    type: Literal["error"] = "error"
    code: str
    message: str


class Status(_Message):
    """What the session can do right now; sent on connect and whenever it changes."""

    type: Literal["status"] = "status"
    muted: bool
    voice: bool  # False when the audio stack could not start (typed input still works)
    hands_free: bool  # wake word active
    push_to_talk_key: str | None  # e.g. "left_ctrl"; None if push-to-talk is off


class UsageUpdate(_Message):
    """Model of the last Claude call and spending so far (for the status bar)."""

    type: Literal["usage"] = "usage"
    model: str
    route: str
    session_cost_usd: float = Field(ge=0)
    cap_usd: float | None


class MemoryFactItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int
    text: str
    category: str
    source: Literal["explicit", "extracted"]
    created_at: str


class MemorySnapshot(_Message):
    """Everything in long-term memory, sent when the HUD asks or after a change it made."""

    type: Literal["memory"] = "memory"
    enabled: bool
    facts: tuple[MemoryFactItem, ...]
    summary: str | None


StopTarget = Literal["listening", "transcription", "reply", "speech", "tools"]


class Stopped(_Message):
    """Acknowledges a stop (HUD Stop button or Esc): what was actually cancelled.

    Empty ``cancelled`` means Jarvis was already idle and nothing changed.
    """

    type: Literal["stopped"] = "stopped"
    cancelled: tuple[StopTarget, ...]


BackendEvent = Annotated[
    StateChanged
    | AudioLevel
    | Transcript
    | ToolStarted
    | ToolFinished
    | CodeBlock
    | Sources
    | ErrorEvent
    | Status
    | UsageUpdate
    | MemorySnapshot
    | Stopped,
    Field(discriminator="type"),
]


# --------------------------------------------------------------------------
# UI -> backend commands
# --------------------------------------------------------------------------


class MuteToggle(_Message):
    type: Literal["mute_toggle"] = "mute_toggle"


class Interrupt(_Message):
    """Stop everything Jarvis is doing right now (HUD Stop button and Esc both send this)."""

    type: Literal["interrupt"] = "interrupt"


class Shutdown(_Message):
    type: Literal["shutdown"] = "shutdown"


class TextInput(_Message):
    type: Literal["text_input"] = "text_input"
    text: str = Field(min_length=1, max_length=4000)


class Auth(_Message):
    """Must be the first message on a connection; carries the per-launch token."""

    type: Literal["auth"] = "auth"
    token: str = Field(min_length=16, max_length=256)


class PttStart(_Message):
    """HUD push-to-talk (Space held): start recording."""

    type: Literal["ptt_start"] = "ptt_start"


class PttStop(_Message):
    type: Literal["ptt_stop"] = "ptt_stop"


class MemoryList(_Message):
    type: Literal["memory_list"] = "memory_list"


class MemoryDelete(_Message):
    type: Literal["memory_delete"] = "memory_delete"
    ids: tuple[int, ...] = Field(min_length=1, max_length=500)


class MemoryForgetAll(_Message):
    """The HUD asks for confirmation itself before sending this."""

    type: Literal["memory_forget_all"] = "memory_forget_all"


ClientCommand = Annotated[
    MuteToggle
    | Interrupt
    | Shutdown
    | TextInput
    | Auth
    | PttStart
    | PttStop
    | MemoryList
    | MemoryDelete
    | MemoryForgetAll,
    Field(discriminator="type"),
]

backend_event_adapter: TypeAdapter[BackendEvent] = TypeAdapter(BackendEvent)
client_command_adapter: TypeAdapter[ClientCommand] = TypeAdapter(ClientCommand)


def protocol_json_schema() -> dict[str, object]:
    """Build the combined JSON Schema document for both message directions."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "J.A.R.V.I.S. event protocol",
        "protocol_version": PROTOCOL_VERSION,
        "backend_event": backend_event_adapter.json_schema(),
        "client_command": client_command_adapter.json_schema(),
    }


# --------------------------------------------------------------------------
# In-process bus
# --------------------------------------------------------------------------

Subscriber = Callable[[BaseModel], Awaitable[None]]


class EventBus:
    """Fan-out of backend events to every front-end (terminal now, WebSocket later).

    Subscribers are awaited in registration order so events arrive in the
    order they were published. A failing subscriber is logged and skipped:
    a broken display must never take down the assistant itself.
    """

    def __init__(self) -> None:
        self._subscribers: list[Subscriber] = []

    def subscribe(self, subscriber: Subscriber) -> None:
        self._subscribers.append(subscriber)

    async def publish(self, event: BaseModel) -> None:
        for subscriber in self._subscribers:
            try:
                await subscriber(event)
            except Exception:
                logger.exception("event subscriber failed", extra={"event_type": _type_of(event)})


def _type_of(event: BaseModel) -> str:
    return str(getattr(event, "type", type(event).__name__))
