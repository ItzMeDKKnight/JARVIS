# Adding a tool

> The `get_time` example below is illustrative only: Jarvis already receives the local
> date and time with every request (`llm.time_note`).

Tools are small classes in `backend/jarvis/tools/`. The registry gives every tool input
validation, narration, a timeout, crash protection and untrusted-output wrapping for free.

## 1. Write the tool

```python
# backend/jarvis/tools/get_time.py
"""get_time: tell the current local time."""

from __future__ import annotations

from datetime import datetime

from pydantic import Field

from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome


class GetTimeInput(ToolInput):
    timezone: str = Field(default="local", description="IANA zone, or 'local'.")


class GetTimeTool(Tool[GetTimeInput]):
    name = "get_time"
    # Tell Claude *when* to call it, not just what it does.
    description = "Get the current time. Call this when the user asks what time or date it is."
    input_model = GetTimeInput

    def __init__(self, *, timeout_s: float) -> None:
        self.timeout_s = timeout_s

    async def run(self, args: GetTimeInput, ctx: ToolContext) -> ToolOutcome:
        now = datetime.now().strftime("%A %d %B, %H:%M")
        return ToolOutcome(content=f"It is {now}.", ok=True, summary="Told the time")
```

Rules:

- **Expected failures return `ok=False`.** Don't raise for them; Claude reads `content` and
  explains the problem. Unexpected exceptions are caught by the registry.
- Set `untrusted=True` when `content` contains text from outside (web pages, files, emails).
- Never pass model-provided strings to a shell, `eval`, or a launcher. Look them up in an
  allowlist instead (see `open_app.py`).
- If the tool touches the OS, hardware or network, put that behind a Protocol in
  `os_adapters/` (or a new package) so tests can use a fake.
- `narration_target()` fills `{target}` in the narration line. Return `None` to stay silent.

## 2. Add narration lines

```yaml
# config/config.yaml
narration:
  get_time:
    en: "Checking the clock."
    hi: "घड़ी देख रहा हूँ।"
```

## 3. Register it

In `build_default_registry` (`backend/jarvis/tools/registry.py`):

```python
registry.register(GetTimeTool(timeout_s=tools_cfg.timeout_s))
```

## 4. Mention it in the persona

Add one line under `# Tools` in `config/persona.md` saying when to use it.

## 5. Test it

Add `backend/tests/test_get_time.py`. Use `tests/fakes.py` for the context, and update
`test_definitions_are_sorted_and_strict` with the new tool name.
