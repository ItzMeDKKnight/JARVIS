"""Check prompt caching against the live API: two requests, the second must hit the cache.

    .venv/Scripts/python scripts/check_caching.py            # routine route (Haiku)
    .venv/Scripts/python scripts/check_caching.py coding     # coding route (Sonnet)

It builds exactly the request prefix Jarvis sends (tool definitions, the core system
prompt, then the personality block) and sends four one-line turns: two in the normal
personality, then two after switching to Evil at 80%.
Expected: request 2 reads the whole prefix from the cache; request 3 (new personality)
still reads the tools and the core, and writes only the changed part (one cache miss);
request 4 reads everything again. If request 2 shows 0, the prefix is below the model's
minimum cacheable length or something in it changes between requests.

Costs well under one US cent. Needs ANTHROPIC_API_KEY (environment or .env).
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path

from jarvis.config import load_api_key, load_config
from jarvis.cost import CostTracker
from jarvis.documents.designer import AnthropicDesigner
from jarvis.documents.pdf_writer import FpdfWriter
from jarvis.events import EventBus
from jarvis.llm.client import AnthropicLLMClient, create_async_client
from jarvis.llm.prompts import build_system_prompt, persona_sections
from jarvis.llm.types import LLMRequest
from jarvis.main import _screen_vision
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import MemoryStore
from jarvis.os_adapters.base import DefaultBrowserOpener, default_launcher
from jarvis.personality import PersonalityState
from jarvis.search.anthropic_search import AnthropicWebSearch
from jarvis.tools.registry import build_default_registry

STEPS = (
    ("normal", 50, "Say only the word one."),
    ("normal", 50, "Say only the word two."),
    ("evil", 80, "Say only the word three."),
    ("evil", 80, "Say only the word four."),
)


async def _ignore(_fragment: str) -> None:
    return None


async def check(route_name: str) -> bool:
    config = load_config()
    sdk = create_async_client(load_api_key(), config.llm)
    cost = CostTracker(config.pricing)
    # A throwaway store: the memory tools must be in the tool list, but nothing is saved.
    with tempfile.TemporaryDirectory() as tmp:
        store = MemoryStore(Path(tmp) / "memory.db", max_facts=1, max_fact_chars=10)
        memory = MemoryService(
            config.memory,
            store,
            None,
            user_name=config.assistant.user_name,
            assistant_name=config.assistant.name,
            history_max_turns=config.llm.history_max_turns,
        )
        search = AnthropicWebSearch(sdk, config.search)
        personality = PersonalityState(persona_sections(config))  # not saved: a throwaway
        registry = build_default_registry(
            config,
            launcher=default_launcher(),
            browser=DefaultBrowserOpener(),
            search=search,
            memory=memory,
            videos=search,
            pdf=FpdfWriter(config.pdf, Path(tmp)),
            designer=AnthropicDesigner(sdk, config.pdf.designer),
            screen=_screen_vision(config, sdk, EventBus(), cost),
            personality=personality,
        )
        llm = AnthropicLLMClient(sdk)
        route = config.llm.routes[route_name]
        reads: list[int] = []
        try:
            for index, (mode, intensity, question) in enumerate(STEPS):
                await personality.set(mode, intensity)  # type: ignore[arg-type]
                request = LLMRequest(
                    route_name=route_name,
                    route=route,
                    system=build_system_prompt(config),
                    tools=registry.definitions(),
                    messages=[{"role": "user", "content": question}],
                    persona=personality.prompt(),
                )
                response = await llm.respond(request, _ignore)
                usage = response.usage
                usd = cost.record(response.model, usage)
                reads.append(usage.cache_read_input_tokens)
                print(
                    f"request {index + 1} ({personality.current.label}): model={response.model} "
                    f"input={usage.input_tokens} cache_write={usage.cache_creation_input_tokens} "
                    f"cache_read={usage.cache_read_input_tokens} cost=${usd:.6f}"
                )
        finally:
            store.close()
            await sdk.close()
    ok = reads[1] > 0 and 0 < reads[2] < reads[1] + 1 and reads[3] > 0
    if ok:
        print(
            "caching works: the personality switch (request 3) still read "
            f"{reads[2]} cached tokens (tools + core) and missed only the changed part"
        )
    else:
        print(f"UNEXPECTED cache pattern: reads={reads}")
    return ok


def main() -> int:
    route = sys.argv[1] if len(sys.argv) > 1 else "routine"
    return 0 if asyncio.run(check(route)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
