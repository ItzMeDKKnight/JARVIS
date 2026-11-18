"""set_personality: switch Jarvis's personality mode and intensity by voice.

"Be witty", "evil mode at 80 percent", "थोड़ा मज़ेदार बनो", "normal ho jao": Claude maps
the request (in any language) to a mode and an intensity. The choice is saved and applies
from the next reply. It changes only how Jarvis talks (see the core of config/persona.md).
"""

from __future__ import annotations

from pydantic import Field

from jarvis.personality import LABELS, Mode, PersonalityState
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome


class SetPersonalityInput(ToolInput):
    mode: Mode = Field(description="normal, witty, evil, joyful or lusty.")
    intensity: int | None = Field(
        default=None,
        ge=0,
        le=100,
        description="0 to 100 percent. Omit to keep the current intensity. Ignored for normal.",
    )


class SetPersonalityTool(Tool[SetPersonalityInput]):
    name = "set_personality"
    description = (
        "Change your personality mode (normal, witty, evil, joyful, lusty) and its intensity "
        "(0 to 100 percent) when the user asks, in any language. It changes only how you talk, "
        "from your next reply; it never changes what you do."
    )
    input_model = SetPersonalityInput

    def __init__(self, state: PersonalityState, *, timeout_s: float) -> None:
        self._state = state
        self.timeout_s = timeout_s

    def narration_target(self, args: SetPersonalityInput) -> str | None:
        return None  # instant; the reply itself shows the new personality

    async def run(self, args: SetPersonalityInput, ctx: ToolContext) -> ToolOutcome:
        previous = self._state.current
        intensity = args.intensity
        if intensity is None and previous.mode == "normal" and args.mode != "normal":
            intensity = 50
        new = await self._state.set(args.mode, intensity)
        what = (
            LABELS[new.mode] if new.mode == "normal" else f"{LABELS[new.mode]} at {new.intensity}%"
        )
        return ToolOutcome(
            content=(
                f"Personality is now {what} (it was {previous.label}). Confirm it in one short "
                "sentence, already speaking in the new personality."
            ),
            ok=True,
            summary=f"Personality: {new.label}",
        )
