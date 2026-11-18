"""MemoryService: the one object the rest of Jarvis talks to about memory.

Responsibilities:

* **Recall**: before each turn, build a short note with the facts relevant to
  what was just said (plus the rolling summary on the first turn). The
  assistant appends it as a mid-conversation system message, so the cached
  system prompt and earlier history stay byte-identical.
* **Collect**: subscribe to the event bus and buffer the final text of each
  utterance, with sensitive sentences removed, for consolidation.
* **Consolidate**: after enough turns, when the conversation pauses (state
  IDLE or LISTENING), run the summary/fact-extraction call in the background.
  On exit, :meth:`flush` runs a last one.
* **Explicit commands**: remember / search / forget, used by the memory tools.

Forgetting must win every race: after a forget, the turns still buffered and
any consolidation already in flight are discarded, so a forgotten fact cannot
be re-extracted from the conversation that mentioned it.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from pydantic import BaseModel

from jarvis.config import MemoryConfig
from jarvis.events import AssistantState, StateChanged, ToolStarted, Transcript
from jarvis.memory.facts import content_words, rank
from jarvis.memory.sensitive import drop_sensitive_sentences
from jarvis.memory.store import AddResult, Fact, MemoryStore
from jarvis.memory.summary import (
    CATEGORIES,
    ConsolidationError,
    ConsolidationResult,
    Consolidator,
    TurnLine,
)

logger = logging.getLogger(__name__)

_PAUSE_STATES = frozenset({AssistantState.IDLE, AssistantState.LISTENING})
# A turn that looked at the screen is never remembered: neither the request nor the reply
# (which may describe what was on the screen). Screenshots never reach memory at all.
SCREEN_TOOLS = frozenset({"look_at_screen", "act_on_screen"})
_SUMMARY_KEY = "summary"


class MemoryService:
    def __init__(
        self,
        config: MemoryConfig,
        store: MemoryStore,
        consolidator: Consolidator | None,
        *,
        user_name: str,
        assistant_name: str,
        history_max_turns: int,
    ) -> None:
        self._config = config
        self._store = store
        self._consolidator = consolidator
        self._user_name = user_name
        self._names = (user_name, assistant_name)
        # A note stays in the conversation until its turn is trimmed from history,
        # so the same fact is only re-sent after that many turns.
        self._reinject_after = history_max_turns
        self._turn = 0
        self._injected: dict[object, int] = {}
        self._pending: list[TurnLine] = []
        self._turn_start = 0  # where the current turn's lines begin in _pending
        self._task: asyncio.Task[None] | None = None
        # Bumped by every forget; results computed before it are discarded.
        self._generation = 0
        self._skip_rest_of_turn = False

    # -- recall -----------------------------------------------------------------------

    async def note_for(self, user_text: str) -> str | None:
        """The memory note to add before answering ``user_text``, or None if nothing fits."""
        self._turn += 1
        summary = await asyncio.to_thread(self._store.summary) if self._due(_SUMMARY_KEY) else None
        facts = await asyncio.to_thread(self._store.facts)
        relevant = rank(
            facts,
            user_text,
            min_score=self._config.recall_min_score,
            limit=self._config.recall_max_facts,
            ignore=self._names,
        )
        fresh = [f for f in relevant if self._due(f.id)]
        if summary is None and not fresh:
            return None
        if summary is not None:
            self._injected[_SUMMARY_KEY] = self._turn
        for fact in fresh:
            self._injected[fact.id] = self._turn
        logger.info(
            "memory recall", extra={"facts": len(fresh), "with_summary": summary is not None}
        )
        return self._render_note(summary, fresh)

    def _due(self, key: object) -> bool:
        last = self._injected.get(key)
        return last is None or self._turn - last >= self._reinject_after

    def _render_note(self, summary: str | None, facts: list[Fact]) -> str:
        lines = [
            f"Memory notes from earlier conversations with {self._user_name}. Use them only "
            "when relevant, and never recite them as a list."
        ]
        if summary is not None:
            lines.append(f"Earlier conversations, summarised: {summary}")
        if facts:
            lines.append(f"Things {self._user_name} has told you:")
            lines.extend(f"- {fact.text}" for fact in facts)
        return "\n".join(lines)

    # -- explicit commands ------------------------------------------------------------

    async def remember(self, text: str, keywords: tuple[str, ...]) -> AddResult:
        return await asyncio.to_thread(
            self._store.add_fact, text, category="other", keywords=keywords, source="explicit"
        )

    async def search(self, query: str | None) -> list[Fact]:
        """Facts matching ``query``; with no query, the most recent ones."""
        facts = await asyncio.to_thread(self._store.facts)
        limit = self._config.search_max_results
        if not query or not query.strip():
            return facts[:limit]
        return rank(facts, query, min_score=1, limit=limit, ignore=self._names)

    async def snapshot(self) -> tuple[list[Fact], str | None]:
        """All facts (newest first) and the summary, for the HUD memory panel."""
        facts = await asyncio.to_thread(self._store.facts)
        summary = await asyncio.to_thread(self._store.summary)
        return facts, summary

    async def forget_ids(self, ids: list[int]) -> int:
        """Delete facts picked in the HUD. Same guarantees as a spoken forget."""
        wanted = set(ids)
        facts = [f for f in await asyncio.to_thread(self._store.facts) if f.id in wanted]
        if not facts:
            return 0
        deleted, _ = await self.forget(
            facts, " ".join(f.text for f in facts), skip_rest_of_turn=False
        )
        return deleted

    async def forget(
        self, facts: list[Fact], query: str, *, skip_rest_of_turn: bool = True
    ) -> tuple[int, bool]:
        """Delete ``facts``; also drop the summary if it mentions the topic.

        Returns ``(deleted_count, summary_cleared)``. A summary cannot be edited
        surgically without another model call, so when it mentions what is
        being forgotten the whole summary goes. It is rebuilt from future
        conversations.
        """
        self._discard_unsaved(skip_rest_of_turn=skip_rest_of_turn)
        deleted = await asyncio.to_thread(self._store.delete_facts, [f.id for f in facts])
        summary = await asyncio.to_thread(self._store.summary)
        topic = content_words(query) - {w for n in self._names for w in content_words(n)}
        summary_cleared = bool(summary and topic & content_words(summary))
        if summary_cleared:
            await asyncio.to_thread(self._store.clear_summary)
        logger.info("memory forget", extra={"deleted": deleted, "summary_cleared": summary_cleared})
        return deleted, summary_cleared

    async def forget_everything(self) -> None:
        self._discard_unsaved()
        await asyncio.to_thread(self._store.forget_everything)
        self._injected.clear()
        logger.info("memory wiped by voice/text command")

    def _discard_unsaved(self, *, skip_rest_of_turn: bool = True) -> None:
        self._generation += 1
        self._pending.clear()
        # The spoken reply confirming a forget would mention the topic again.
        # (A deletion in the HUD has no such reply, so the next turn is kept.)
        self._skip_rest_of_turn = skip_rest_of_turn

    # -- collecting turns (event-bus subscriber) --------------------------------------

    async def __call__(self, event: BaseModel) -> None:
        if isinstance(event, Transcript) and event.final:
            self._buffer(event)
        elif isinstance(event, ToolStarted) and event.name in SCREEN_TOOLS:
            del self._pending[self._turn_start :]
            self._skip_rest_of_turn = True
        elif isinstance(event, StateChanged) and event.state in _PAUSE_STATES:
            self._skip_rest_of_turn = False
            self._maybe_consolidate(force=False)

    def _buffer(self, event: Transcript) -> None:
        if self._skip_rest_of_turn:
            return
        if event.role == "user":
            self._turn_start = len(self._pending)
        text = drop_sensitive_sentences(event.text)
        if not text:
            return
        self._pending.append(TurnLine(role=event.role, text=text))
        overflow = len(self._pending) - self._config.max_buffered_messages
        if overflow > 0:
            del self._pending[:overflow]

    # -- consolidation ------------------------------------------------------------------

    def _maybe_consolidate(self, *, force: bool) -> None:
        consolidator = self._consolidator
        if consolidator is None or (self._task is not None and not self._task.done()):
            return
        user_lines = sum(1 for line in self._pending if line.role == "user")
        if user_lines == 0 or (not force and user_lines < self._config.consolidate_every_turns):
            return
        lines, self._pending = self._pending, []
        self._task = asyncio.create_task(self._consolidate(consolidator, lines, self._generation))

    async def _consolidate(
        self, consolidator: Consolidator, lines: list[TurnLine], generation: int
    ) -> None:
        try:
            summary = await asyncio.to_thread(self._store.summary)
            facts = await asyncio.to_thread(self._store.facts)
            result = await consolidator.consolidate(summary, facts, lines)
        except ConsolidationError as exc:
            logger.warning("memory consolidation failed; will retry", extra={"reason": str(exc)})
            self._requeue(lines, generation)
            return
        except Exception:
            logger.exception("memory consolidation crashed")
            self._requeue(lines, generation)
            return
        if generation != self._generation:
            logger.info("memory consolidation discarded after a forget")
            return
        await asyncio.to_thread(self._apply, result, facts)

    def _requeue(self, lines: list[TurnLine], generation: int) -> None:
        if generation != self._generation:
            return
        self._pending = (lines + self._pending)[-self._config.max_buffered_messages :]

    def _apply(self, result: ConsolidationResult, known: list[Fact]) -> None:
        self._store.set_summary(result.summary, max_chars=self._config.max_summary_chars)
        known_ids = {fact.id for fact in known}
        outdated = [i for i in result.outdated_fact_ids if i in known_ids]
        self._store.delete_facts(outdated)
        statuses: dict[str, int] = {}
        for extracted in result.new_facts:
            category = extracted.category if extracted.category in CATEGORIES else "other"
            outcome = self._store.add_fact(
                extracted.text,
                category=category,
                keywords=tuple(extracted.keywords),
                source="extracted",
            )
            statuses[outcome.status] = statuses.get(outcome.status, 0) + 1
        logger.info("memory consolidated", extra={"outdated_removed": len(outdated), **statuses})

    async def flush(self, timeout_s: float | None = None) -> None:
        """Consolidate whatever is still buffered and wait for it (used on exit)."""
        self._maybe_consolidate(force=True)
        task = self._task
        if task is None or task.done():
            return
        try:
            await asyncio.wait_for(
                asyncio.shield(task), timeout_s or self._config.shutdown_flush_timeout_s
            )
        except TimeoutError:
            logger.warning("final memory consolidation timed out; recent turns not summarised")
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
