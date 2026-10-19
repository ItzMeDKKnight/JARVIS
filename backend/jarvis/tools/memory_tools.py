"""remember / recall_memory / forget_memory: the voice commands for long-term memory.

They are ordinary tools rather than keyword commands so that every phrasing
works in English, Hindi and Hinglish ("yaad rakhna ki...", "भूल जाओ...") without
a parser of our own. The safety rules live below the model:

* the store refuses sensitive data no matter what the model sends;
* forgetting several notes, or everything, needs a spoken/typed "yes".

They have no narration line: they finish in milliseconds, so there is no
silence to fill, and the model's one-sentence reply reports the outcome.
"""

from __future__ import annotations

import asyncio

from pydantic import Field

from jarvis.config import LocalizedText
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import Fact
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome

_BLOCKED_REPLY = (
    "BLOCKED: this looks like {categories}. Nothing was stored. Politely tell the user in one "
    "sentence that you never store passwords, PINs, ID numbers or card numbers, and do not "
    "repeat the value back."
)


def _bullets(facts: list[Fact]) -> str:
    return "\n".join(f"- {fact.text}" for fact in facts)


class RememberInput(ToolInput):
    fact: str = Field(
        min_length=1,
        max_length=300,
        description="What to remember, as one short third-person sentence in English, "
        'e.g. "Tony\'s sister is called Meera."',
    )
    keywords: list[str] = Field(
        default_factory=list,
        max_length=12,
        description="3 to 8 words someone might use when this becomes relevant, in English "
        "and in Hindi (Devanagari).",
    )


class RememberTool(Tool[RememberInput]):
    name = "remember"
    description = (
        "Save a fact about the user to long-term memory. Call this only when the user "
        "explicitly asks you to remember something ('remember that...', 'yaad rakhna...')."
    )
    input_model = RememberInput

    def __init__(self, memory: MemoryService, *, timeout_s: float) -> None:
        self._memory = memory
        self.timeout_s = timeout_s

    async def run(self, args: RememberInput, ctx: ToolContext) -> ToolOutcome:
        result = await self._memory.remember(args.fact, tuple(args.keywords))
        if result.status == "blocked":
            categories = ", ".join(c.replace("_", " ") for c in result.blocked_categories)
            return ToolOutcome(
                content=_BLOCKED_REPLY.format(categories=categories),
                ok=False,
                summary="Refused to store sensitive data",
            )
        if result.status == "full":
            return ToolOutcome(
                content="Memory is full; nothing was stored. Suggest forgetting old notes.",
                ok=False,
                summary="Memory full",
            )
        if result.status == "duplicate":
            return ToolOutcome(content="Already remembered.", ok=True, summary="Already known")
        return ToolOutcome(content="Remembered.", ok=True, summary="Remembered")


class RecallInput(ToolInput):
    query: str | None = Field(
        default=None,
        max_length=200,
        description="The topic, e.g. 'my sister' or 'college'. Leave empty to list the most "
        "recent notes.",
    )


class RecallMemoryTool(Tool[RecallInput]):
    name = "recall_memory"
    description = (
        "Look up what you remember about the user. Call this when the user asks what you "
        "remember, or when answering depends on something personal they may have told you "
        "before and the memory notes in the conversation do not cover it."
    )
    input_model = RecallInput

    def __init__(self, memory: MemoryService, *, timeout_s: float) -> None:
        self._memory = memory
        self.timeout_s = timeout_s

    async def run(self, args: RecallInput, ctx: ToolContext) -> ToolOutcome:
        facts = await self._memory.search(args.query)
        if not facts:
            topic = f" about '{args.query}'" if args.query else ""
            return ToolOutcome(
                content=f"No stored notes{topic}.", ok=True, summary="Nothing remembered"
            )
        return ToolOutcome(
            content=f"Stored notes:\n{_bullets(facts)}",
            ok=True,
            summary=f"Recalled {len(facts)} notes",
        )


class ForgetInput(ToolInput):
    query: str | None = Field(
        default=None,
        max_length=200,
        description="What to forget, e.g. 'my sister'. Leave empty only when everything=true.",
    )
    everything: bool = Field(
        default=False,
        description="True only if the user asks to forget everything you remember about them.",
    )


class ForgetMemoryTool(Tool[ForgetInput]):
    name = "forget_memory"
    description = (
        "Delete notes from long-term memory when the user asks you to forget something "
        "('forget that...', 'bhool jao...'), or everything when they ask for that. The user "
        "is asked to confirm before several notes or everything are deleted."
    )
    input_model = ForgetInput
    timeout_s = None  # may wait for a confirmation, which has its own timeout

    def __init__(
        self,
        memory: MemoryService,
        *,
        confirm_timeout_s: float,
        confirm_matches: LocalizedText,
        confirm_all: LocalizedText,
    ) -> None:
        self._memory = memory
        self._confirm_timeout_s = confirm_timeout_s
        self._confirm_matches = confirm_matches
        self._confirm_all = confirm_all

    async def run(self, args: ForgetInput, ctx: ToolContext) -> ToolOutcome:
        if args.everything:
            return await self._forget_everything(ctx)
        if not args.query:
            return ToolOutcome(
                content="Nothing specified to forget. Ask the user what to forget.",
                ok=False,
                summary="Nothing to forget",
            )
        matches = await self._memory.search(args.query)
        if not matches:
            # Still clear the summary and unsaved turns if they mention it.
            _, summary_cleared = await self._memory.forget([], args.query)
            extra = " Removed it from the conversation summary." if summary_cleared else ""
            return ToolOutcome(
                content=f"No stored notes about '{args.query}'.{extra}",
                ok=True,
                summary="No matching notes",
            )
        if len(matches) > 1:
            question = (
                self._confirm_matches.for_language(ctx.language)
                .replace("{count}", str(len(matches)))
                .replace("{target}", args.query)
            )
            if not await self._confirm(question, ctx):
                return ToolOutcome(
                    content="The user chose not to forget them. Nothing was deleted.",
                    ok=True,
                    summary="Forget declined",
                )
        deleted, summary_cleared = await self._memory.forget(matches, args.query)
        lines = [f"Forgot {deleted} notes:", _bullets(matches)]
        if summary_cleared:
            lines.append("Also cleared the conversation summary, which mentioned it.")
        lines.append("Do not use this information again.")
        return ToolOutcome(content="\n".join(lines), ok=True, summary=f"Forgot {deleted} notes")

    async def _forget_everything(self, ctx: ToolContext) -> ToolOutcome:
        if not await self._confirm(self._confirm_all.for_language(ctx.language), ctx):
            return ToolOutcome(
                content="The user chose not to erase memory. Nothing was deleted.",
                ok=True,
                summary="Wipe declined",
            )
        await self._memory.forget_everything()
        return ToolOutcome(
            content="All stored notes and the conversation summary were erased.",
            ok=True,
            summary="Memory erased",
        )

    async def _confirm(self, question: str, ctx: ToolContext) -> bool:
        try:
            return await asyncio.wait_for(ctx.confirmer.confirm(question), self._confirm_timeout_s)
        except TimeoutError:
            return False
