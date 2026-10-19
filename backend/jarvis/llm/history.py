"""In-memory conversation history for the current session.

Long-term context (rolling summary, facts) lives in jarvis.memory and enters
the conversation as *system notes*: mid-conversation ``role: "system"``
messages placed right after the user turn they belong to. Appending them,
rather than editing the system prompt, keeps the cached prefix intact.

The history is append-only between trims. Two API rules make that important:

* prompt caching only hits when the earlier messages are byte-identical;
* thinking blocks are bound to the exact conversation that produced them, and
  replaying one after an edit to earlier messages is rejected.

When the history grows past the configured number of turns, the oldest turns
are dropped *and* every remaining thinking block is removed, which is the
"simple compaction" shape the API accepts.
"""

from __future__ import annotations

from typing import Any

_THINKING_TYPES = frozenset({"thinking", "redacted_thinking"})


def _block_type(block: object) -> str | None:
    if isinstance(block, dict):
        value = block.get("type")
        return value if isinstance(value, str) else None
    return getattr(block, "type", None)


def _block_field(block: object, name: str) -> object:
    if isinstance(block, dict):
        return block.get(name)
    return getattr(block, name, None)


def _is_user_text_turn(message: dict[str, Any]) -> bool:
    """A real user utterance, as opposed to a user message carrying tool results."""
    return message["role"] == "user" and isinstance(message["content"], str)


class ConversationHistory:
    def __init__(self, max_turns: int) -> None:
        self._max_turns = max_turns
        self._messages: list[dict[str, Any]] = []

    @property
    def messages(self) -> list[dict[str, Any]]:
        """A shallow copy, so callers cannot edit history behind our back."""
        return list(self._messages)

    def add_user_text(self, text: str) -> None:
        self._drop_dangling_note()
        self._messages.append({"role": "user", "content": text})
        self._trim()

    def add_system_note(self, text: str) -> None:
        """Attach a memory note to the user turn just added.

        The API accepts a system message only right after a user message, and
        only as the last message or followed by an assistant turn. The
        assistant always answers straight after the note, so both hold.
        """
        if not self._messages or not _is_user_text_turn(self._messages[-1]):
            raise ValueError("a system note must directly follow a user utterance")
        self._messages.append({"role": "system", "content": text})

    def add_assistant(self, content: list[Any]) -> None:
        self._messages.append({"role": "assistant", "content": content})

    def add_tool_results(self, results: list[dict[str, Any]]) -> None:
        # All results of one assistant turn go in a single user message, as the API expects.
        self._messages.append({"role": "user", "content": results})

    def close_interrupted_turn(self) -> None:
        """Keep the history valid after the user interrupts a turn.

        If the last message is an assistant turn with tool calls whose results
        were never added, the API would reject the next request, because every
        tool_use needs a matching tool_result. Add "interrupted" results for them.
        A note left without its assistant reply is removed (see
        :meth:`_drop_dangling_note`).
        """
        self._drop_dangling_note()
        if not self._messages or self._messages[-1]["role"] != "assistant":
            return
        tool_ids = [
            _block_field(block, "id")
            for block in self._messages[-1]["content"]
            if _block_type(block) == "tool_use"
        ]
        if tool_ids:
            self.add_tool_results(
                [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_id,
                        "content": "Interrupted by the user before this ran.",
                        "is_error": True,
                    }
                    for tool_id in tool_ids
                ]
            )

    def _drop_dangling_note(self) -> None:
        """Remove a trailing system note whose turn failed before the assistant replied.

        A user message after it would break the placement rule. Removing it is
        not a harmful history edit: nothing was generated after it, so no
        cached response or thinking block depends on it.
        """
        if self._messages and self._messages[-1]["role"] == "system":
            self._messages.pop()

    def _trim(self) -> None:
        turn_starts = [i for i, m in enumerate(self._messages) if _is_user_text_turn(m)]
        excess = len(turn_starts) - self._max_turns
        if excess <= 0:
            return
        self._messages = self._messages[turn_starts[excess] :]
        for message in self._messages:
            if message["role"] == "assistant":
                message["content"] = [
                    b for b in message["content"] if _block_type(b) not in _THINKING_TYPES
                ]
