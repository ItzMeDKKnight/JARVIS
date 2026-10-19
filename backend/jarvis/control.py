"""Session control: what the HUD can ask the running session to do.

Two implementations:

* :class:`jarvis.voice.VoiceSession`: the normal case, voice plus typed input;
* :class:`TextSession`: typed input only, used when the audio stack can't start
  (no microphone, missing models). The HUD stays usable either way.

Both serialise turns: a new request interrupts the one in progress, exactly
like speaking over Jarvis does.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import Protocol

from jarvis.assistant import Assistant
from jarvis.confirm import is_yes
from jarvis.events import AssistantState, EventBus, Status, Transcript
from jarvis.state import StateMachine
from jarvis.utils.language import detect_language

logger = logging.getLogger(__name__)

S = AssistantState


class SessionControl(Protocol):
    def status(self) -> Status: ...
    async def toggle_mute(self) -> None: ...
    async def interrupt(self) -> None: ...
    async def submit_text(self, text: str) -> None: ...
    async def ptt_start(self) -> None: ...
    async def ptt_stop(self) -> None: ...


class TextSession:
    """Typed-input session for the HUD when voice is unavailable.

    Also the confirmer for its tools: a yes/no question is shown as an
    assistant line, and the next typed input is taken as the answer.
    """

    def __init__(self, *, bus: EventBus, state: StateMachine) -> None:
        self._bus = bus
        self._state = state
        self._assistant: Assistant | None = None
        self._turn: asyncio.Task[None] | None = None
        self._answer: asyncio.Future[str] | None = None
        self._muted = False

    def attach(self, assistant: Assistant) -> None:
        self._assistant = assistant

    async def run(self) -> None:
        """Nothing to poll: wait until cancelled, then stop any running turn."""
        try:
            await asyncio.Event().wait()
        finally:
            await self.interrupt()

    # -- SessionControl ---------------------------------------------------------------

    def status(self) -> Status:
        return Status(muted=self._muted, voice=False, hands_free=False, push_to_talk_key=None)

    async def toggle_mute(self) -> None:
        """No microphone to close, but the state still shows MUTED so the HUD agrees."""
        self._muted = not self._muted
        if self._turn is None or self._turn.done():
            await self._settle()
        await self._bus.publish(self.status())

    async def interrupt(self) -> None:
        self._deliver_answer("")
        turn, self._turn = self._turn, None
        if turn is not None and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn

    async def submit_text(self, text: str) -> None:
        text = text.strip()
        if not text:
            return
        if self._answer is not None and not self._answer.done():
            await self._publish_user(text)
            self._deliver_answer(text)
            return
        await self.interrupt()
        if self._assistant is None:
            raise RuntimeError("TextSession.attach() was not called")
        turn = asyncio.create_task(self._run_turn(self._assistant, text), name="turn")
        self._turn = turn

    async def ptt_start(self) -> None:
        return None  # no microphone in text mode

    async def ptt_stop(self) -> None:
        return None

    # -- Confirmer -------------------------------------------------------------------

    async def confirm(self, question: str) -> bool:
        await self._bus.publish(
            Transcript(
                turn_id=uuid.uuid4().hex[:12],
                role="assistant",
                text=question,
                language=detect_language(question),
                final=True,
            )
        )
        self._answer = asyncio.get_running_loop().create_future()
        try:
            return is_yes(await self._answer)
        finally:
            self._answer = None

    # -- helpers ------------------------------------------------------------------------

    async def _run_turn(self, assistant: Assistant, text: str) -> None:
        await assistant.handle_text(text)
        await self._settle()

    async def _settle(self) -> None:
        if self._muted:
            if self._state.state not in (S.IDLE, S.MUTED):
                await self._state.transition(S.IDLE)
            await self._state.transition(S.MUTED)
        elif self._state.state is S.MUTED:
            await self._state.transition(S.IDLE)

    async def _publish_user(self, text: str) -> None:
        await self._bus.publish(
            Transcript(
                turn_id="answer", role="user", text=text, language=detect_language(text), final=True
            )
        )

    def _deliver_answer(self, text: str) -> None:
        if self._answer is not None and not self._answer.done():
            self._answer.set_result(text)
