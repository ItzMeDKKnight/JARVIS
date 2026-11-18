"""The conversation loop: one user utterance in, spoken replies and actions out.

This is front-end agnostic. It receives text (typed, or transcribed speech),
talks to the LLM, runs tools through the registry, and reports everything as
events. Speech output goes through a :class:`~jarvis.speech.Speaker`, which in
text mode is silent and in voice mode drives TTS and playback.

State rules for one turn:
* THINKING from the moment the utterance arrives;
* SPEAKING when output actually reaches the user (first audio in voice mode);
* back to THINKING while a tool runs, once any speech before it has finished;
* IDLE after the last word has been heard (or the turn was interrupted).

Context note: before the first model call of a turn, a system note is added
after the user's message with the current local date and time and any
relevant long-term memories (never by editing the system prompt, which must
stay byte-identical for caching).

Cost: every model call is priced into the session totals. The output tokens
of one turn, summed over its tool rounds, are capped by
``llm.max_output_tokens_per_turn``, and no call is made whose worst case could
push the session past ``budget.session_cap_usd``. Crossing the warning
threshold is announced once, at the end of that turn.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from jarvis.config import JarvisConfig, LocalizedText
from jarvis.cost import CostTracker, approx_chars
from jarvis.events import (
    AssistantState,
    ErrorEvent,
    EventBus,
    ToolStarted,
    Transcript,
    UsageUpdate,
)
from jarvis.llm.history import ConversationHistory
from jarvis.llm.prompts import build_system_prompt
from jarvis.llm.router import ModelRouter
from jarvis.llm.types import LLMClient, LLMError, LLMRequest, LLMResponse
from jarvis.memory.manager import MemoryService
from jarvis.speech import Speaker, TextSpeaker
from jarvis.state import StateMachine
from jarvis.text import matches_phrase
from jarvis.tools.base import Confirmer, ToolContext
from jarvis.tools.registry import ToolRegistry
from jarvis.utils.language import Language, detect_language
from jarvis.utils.logging import transcripts_enabled
from jarvis.utils.timing import Stopwatch

logger = logging.getLogger(__name__)

S = AssistantState


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


def _local_now() -> datetime:
    return datetime.now().astimezone()


def format_time_note(now: datetime) -> str:
    """One line Claude can use for "what's the date / time" without a web search."""
    offset = now.strftime("%z")  # e.g. +0530
    zone = f"UTC{offset[:3]}:{offset[3:]}" if offset else "local time"
    return f"Current local date and time: {now:%A, %d %B %Y, %H:%M} ({zone})."


def _dollars(value: float) -> str:
    return f"{value:.2f}"


class _Utterance:
    """Accumulates one streamed assistant reply and publishes partial transcripts."""

    def __init__(self, bus: EventBus, language: Language) -> None:
        self._bus = bus
        self._language = language
        self.turn_id = _new_id()
        self.text = ""

    async def add(self, fragment: str) -> None:
        self.text += fragment
        await self._publish(final=False)

    async def finish(self) -> None:
        if self.text.strip():
            await self._publish(final=True)

    async def _publish(self, *, final: bool) -> None:
        await self._bus.publish(
            Transcript(
                turn_id=self.turn_id,
                role="assistant",
                text=self.text,
                language=self._language,
                final=final,
            )
        )


class Assistant:
    def __init__(
        self,
        config: JarvisConfig,
        *,
        llm: LLMClient,
        registry: ToolRegistry,
        bus: EventBus,
        state: StateMachine,
        confirmer: Confirmer,
        speaker: Speaker | None = None,
        memory: MemoryService | None = None,
        cost: CostTracker | None = None,
        clock: Callable[[], datetime] = _local_now,
    ) -> None:
        self._config = config
        self._memory = memory
        self._cost = cost if cost is not None else CostTracker(config.pricing, config.budget)
        self._clock = clock
        self._llm = llm
        self._registry = registry
        self._bus = bus
        self._state = state
        self._confirmer = confirmer
        self._speaker = speaker if speaker is not None else TextSpeaker()
        self._speaker.set_listener(self)
        self._router = ModelRouter(config.llm)
        self._history = ConversationHistory(config.llm.history_max_turns)
        # Built once: both must stay byte-identical across turns for prompt caching.
        self._system_prompt = build_system_prompt(config)
        self._tool_definitions = registry.definitions()
        # Per-turn state.
        self._turn_active = False
        self._tools_running = False
        self._language: Language = "en"
        self._timer = Stopwatch()
        self._end_state: AssistantState = S.IDLE
        bus.subscribe(self._on_event)

    # -- public API ---------------------------------------------------------------

    @property
    def tools_running(self) -> bool:
        """True while this turn's tool calls are executing (reported by a stop)."""
        return self._turn_active and self._tools_running

    async def handle_text(
        self,
        text: str,
        *,
        language: Language | None = None,
        timer: Stopwatch | None = None,
        then: AssistantState = S.IDLE,
    ) -> None:
        """Process one user utterance end to end.

        ``language`` comes from speech recognition when available; typed text is
        classified by script. ``timer`` lets the voice loop start the latency
        clock at the end of speech. ``then`` is the state after the last word is
        heard: LISTENING when a hands-free follow-up window comes next. Never
        raises, except CancelledError when the turn is interrupted (after
        cleaning up, ending in IDLE).
        """
        text = text.strip()
        if not text:
            return
        self._language = language or detect_language(text)
        self._timer = timer or Stopwatch()
        await self._bus.publish(
            Transcript(
                turn_id=_new_id(), role="user", text=text, language=self._language, final=True
            )
        )
        await self._begin_turn(then)
        try:
            if await self._screen_vision_off_command(text):
                return
            await self._converse(text)
            await self._maybe_warn_budget()
        except asyncio.CancelledError:
            self._end_state = S.IDLE
            await self._speaker.stop()
            self._history.close_interrupted_turn()
            logger.info("turn interrupted")
            raise
        except LLMError as exc:
            logger.warning("LLM call failed", extra={"code": exc.code})
            await self._speak_failure("llm_" + exc.code, self._config.messages.api_unreachable)
        except Exception:
            logger.exception("unexpected error while handling a turn")
            await self._speak_failure("internal", self._config.messages.internal_error)
        finally:
            await self._end_turn()

    async def _screen_vision_off_command(self, text: str) -> bool:
        """'Turn off screen vision' while it is on: switch off now, no Claude call needed."""
        gate = self._registry.screen_gate
        if gate is None or not gate.enabled:
            return False
        if not matches_phrase(text, self._config.vision.off_phrases):
            return False
        await gate.disable(source="voice")
        await self._speak_line(self._config.messages.screen_vision_off.for_language(self._language))
        return True

    async def announce(
        self, line: str, language: Language, *, then: AssistantState = S.IDLE
    ) -> None:
        """Speak a fixed line as a short turn of its own (greeting, "I didn't catch that")."""
        self._language = language
        self._timer = Stopwatch()
        await self._begin_turn(then)
        try:
            await self._speak_line(line)
        finally:
            await self._end_turn()

    # -- SpeakerListener ------------------------------------------------------------

    async def audio_started(self) -> None:
        self._timer.mark("first_audio")
        if self._turn_active and self._state.state != S.SPEAKING:
            await self._state.transition(S.SPEAKING)

    async def audio_idle(self) -> None:
        # Only meaningful while a tool is running: speech before it has finished.
        if self._turn_active and self._tools_running and self._state.state == S.SPEAKING:
            await self._state.transition(S.THINKING)

    # -- turn mechanics -------------------------------------------------------------

    async def _begin_turn(self, then: AssistantState) -> None:
        self._turn_active = True
        self._tools_running = False
        self._end_state = then
        await self._state.transition(S.THINKING)

    async def _end_turn(self) -> None:
        try:
            await self._speaker.drain()
        finally:
            self._turn_active = False
            self._tools_running = False
            await self._state.transition(self._end_state)
            logger.info(
                "turn timing", extra={"total_ms": self._timer.elapsed_ms(), **self._timer.as_ms()}
            )

    async def _on_event(self, event: BaseModel) -> None:
        """Speak each tool's narration line the moment the tool starts."""
        if isinstance(event, ToolStarted) and event.summary and self._turn_active:
            await self._speaker.say(event.summary, self._language)

    async def _converse(self, text: str) -> None:
        route_name, route = self._router.route(text)
        self._history.add_user_text(text)
        await self._add_context_note(text)
        ctx = ToolContext(
            bus=self._bus, confirmer=self._confirmer, language=self._language, user_text=text
        )
        budget = self._config.llm.max_output_tokens_per_turn

        for _round in range(self._config.llm.max_tool_rounds + 1):
            if budget < self._config.llm.min_tokens_per_round:
                logger.warning("turn output budget exhausted", extra={"route": route_name})
                await self._speak_line(
                    self._config.messages.turn_budget_exhausted.for_language(self._language)
                )
                return
            request = LLMRequest(
                route_name=route_name,
                route=route.model_copy(update={"max_tokens": min(route.max_tokens, budget)}),
                system=self._system_prompt,
                tools=self._tool_definitions,
                messages=self._history.messages,
                persona=self._persona_block(),
            )
            if not self._affordable(request):
                cap = _dollars(self._cost.cap_usd or 0.0)
                line = self._config.messages.budget_exhausted.for_language(self._language)
                await self._speak_line(line.replace("{cap}", cap))
                return
            response = await self._respond(request)
            budget -= response.usage.output_tokens

            if response.stop_reason == "refusal":
                # Do not keep a refused (possibly partial) reply in the history.
                await self._speaker.stop()
                await self._speak_line(self._config.messages.refusal.for_language(self._language))
                return
            self._history.add_assistant(response.assistant_content)

            if not response.tool_calls:
                return
            if response.stop_reason == "max_tokens":
                # A truncated tool input still parses as a (wrong) partial object; never run it.
                raise LLMError("bad_output", "response was cut off inside a tool call")

            results = await self._run_tools(response, ctx)
            self._history.add_tool_results(results)

        logger.warning(
            "tool round limit reached", extra={"limit": self._config.llm.max_tool_rounds}
        )
        await self._speak_line(self._config.messages.internal_error.for_language(self._language))

    def _persona_block(self) -> str:
        """The active personality, read fresh for every request (so a change applies next)."""
        personality = self._registry.personality
        return personality.prompt() if personality is not None else ""

    async def _add_context_note(self, text: str) -> None:
        """Attach the date/time and relevant memories. Memory trouble never blocks a reply."""
        parts: list[str] = []
        if self._config.llm.time_note:
            parts.append(format_time_note(self._clock()))
        if self._memory is not None:
            try:
                memory_note = await self._memory.note_for(text)
            except Exception:
                logger.exception("memory recall failed; answering without it")
                memory_note = None
            if memory_note:
                parts.append(memory_note)
        if parts:
            self._history.add_system_note("\n\n".join(parts))

    def _affordable(self, request: LLMRequest) -> bool:
        """Strict budget check: the call's worst case must fit in what is left."""
        worst = self._cost.worst_case(
            request.route.model,
            max_output_tokens=request.route.max_tokens,
            input_chars=approx_chars([request.system, request.tools, request.messages]),
        )
        return self._cost.can_afford(worst)

    async def _maybe_warn_budget(self) -> None:
        if not self._cost.take_budget_warning():
            return
        line = (
            self._config.messages.budget_warning.for_language(self._language)
            .replace("{spent}", _dollars(self._cost.session_usd))
            .replace("{cap}", _dollars(self._cost.cap_usd or 0.0))
        )
        await self._speak_line(line)

    async def _respond(self, request: LLMRequest) -> LLMResponse:
        """One streamed model call; text goes to the transcript and the speaker as it arrives."""
        utterance = _Utterance(self._bus, self._language)
        call_timer = Stopwatch()

        async def on_text(fragment: str) -> None:
            call_timer.mark("first_token")
            self._timer.mark("first_token")
            await utterance.add(fragment)
            await self._speaker.feed(fragment, self._language)

        response = await self._llm.respond(request, on_text)
        await self._speaker.end_utterance()
        await utterance.finish()
        self._log_call(request, response, call_timer)
        await self._bus.publish(
            UsageUpdate(
                model=response.model,
                route=request.route_name,
                session_cost_usd=round(self._cost.session_usd, 6),
                cap_usd=self._cost.cap_usd,
            )
        )
        return response

    async def _run_tools(self, response: LLMResponse, ctx: ToolContext) -> list[dict[str, Any]]:
        self._tools_running = True
        if not self._speaker.is_playing:
            await self._state.transition(S.THINKING)
        try:
            return [
                await self._run_tool(call.id, call.name, call.input, ctx)
                for call in response.tool_calls
            ]
        finally:
            self._tools_running = False

    async def _run_tool(
        self, call_id: str, name: str, raw_input: object, ctx: ToolContext
    ) -> dict[str, Any]:
        outcome = await self._registry.execute(call_id, name, raw_input, ctx)
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": call_id,
            "content": outcome.content,
        }
        if not outcome.ok:
            result["is_error"] = True
        return result

    async def _speak_line(self, line: str) -> None:
        """Say a fixed line (fallbacks, refusals) as a complete assistant utterance."""
        utterance = _Utterance(self._bus, self._language)
        await utterance.add(line)
        await self._speaker.say(line, self._language)
        await utterance.finish()

    async def _speak_failure(self, code: str, line_by_language: LocalizedText) -> None:
        line = line_by_language.for_language(self._language)
        await self._speaker.stop()
        await self._bus.publish(ErrorEvent(code=code, message=line))
        await self._state.transition(S.ERROR)
        await self._speak_line(line)

    def _log_call(self, request: LLMRequest, response: LLMResponse, stopwatch: Stopwatch) -> None:
        usage = response.usage
        cost_usd = self._cost.record(response.model, usage)
        extra: dict[str, Any] = {
            "route": request.route_name,
            "requested_model": request.route.model,
            "served_model": response.model,
            "stop_reason": response.stop_reason,
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "tool_calls": [call.name for call in response.tool_calls],
            "cost_usd": round(cost_usd, 6),
            "session_cost_usd": round(self._cost.session_usd, 6),
            "total_ms": stopwatch.elapsed_ms(),
            **stopwatch.as_ms(),
        }
        if transcripts_enabled():
            extra["assistant_text"] = response.text
        logger.info("llm call", extra=extra)
