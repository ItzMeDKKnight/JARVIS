"""show_code: put code on screen instead of reading it aloud.

The code is only displayed (terminal now, the HUD code panel in Phase 5).
It is never executed.
"""

from __future__ import annotations

from pydantic import Field

from jarvis.events import CodeBlock
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome

MAX_CODE_CHARS = 50_000


class ShowCodeInput(ToolInput):
    title: str = Field(min_length=1, max_length=120, description="Short title for the code panel.")
    language: str = Field(
        min_length=1,
        max_length=30,
        description="Language name for syntax highlighting, e.g. python, javascript, sql.",
    )
    code: str = Field(min_length=1, max_length=MAX_CODE_CHARS, description="The complete code.")


class ShowCodeTool(Tool[ShowCodeInput]):
    name = "show_code"
    description = (
        "Display code on the user's screen with syntax highlighting and a copy button. Call this "
        "for every answer that includes code, after a short spoken explanation. The code is shown, "
        "never executed and never read aloud."
    )
    input_model = ShowCodeInput

    def __init__(self, *, timeout_s: float) -> None:
        self.timeout_s = timeout_s

    async def run(self, args: ShowCodeInput, ctx: ToolContext) -> ToolOutcome:
        await ctx.bus.publish(CodeBlock(title=args.title, language=args.language, code=args.code))
        return ToolOutcome(
            content="The code is now displayed on the user's screen.",
            ok=True,
            summary=f"Showed code: {args.title}",
        )
