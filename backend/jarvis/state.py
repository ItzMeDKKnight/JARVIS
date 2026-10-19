"""The assistant's state machine: the single source of truth for what Jarvis is doing.

Front-ends never infer state from other events; they render whatever
``state_changed`` says. Keeping the allowed transitions in one table makes
illegal jumps (for example SPEAKING straight to THINKING) fail loudly in tests
instead of producing a confused UI.
"""

from __future__ import annotations

from jarvis.events import AssistantState, EventBus, StateChanged

S = AssistantState

ALLOWED_TRANSITIONS: dict[AssistantState, frozenset[AssistantState]] = {
    S.IDLE: frozenset({S.LISTENING, S.THINKING, S.MUTED, S.ERROR}),
    # THINKING is reachable from IDLE directly for typed input (no listening phase).
    S.LISTENING: frozenset({S.THINKING, S.IDLE, S.MUTED, S.ERROR}),
    # THINKING -> LISTENING: a tool asked a yes/no question and waits for the answer.
    S.THINKING: frozenset({S.SPEAKING, S.LISTENING, S.IDLE, S.ERROR}),
    # SPEAKING -> THINKING covers a tool call after Jarvis has started talking.
    S.SPEAKING: frozenset({S.LISTENING, S.THINKING, S.IDLE, S.ERROR}),
    S.ERROR: frozenset({S.IDLE, S.SPEAKING}),
    # MUTED -> THINKING: typed input from the HUD still works while the mic is off.
    S.MUTED: frozenset({S.IDLE, S.THINKING}),
}


class InvalidTransitionError(RuntimeError):
    """Raised when code tries a transition the table does not allow."""


class StateMachine:
    """Holds the current state and announces every change on the event bus."""

    def __init__(self, bus: EventBus, initial: AssistantState = S.IDLE) -> None:
        self._bus = bus
        self._state = initial

    @property
    def state(self) -> AssistantState:
        return self._state

    async def transition(self, target: AssistantState) -> None:
        """Move to ``target``; re-entering the current state is a silent no-op."""
        if target == self._state:
            return
        if target not in ALLOWED_TRANSITIONS[self._state]:
            raise InvalidTransitionError(f"{self._state.value} -> {target.value} is not allowed")
        self._state = target
        await self._bus.publish(StateChanged(state=target))
