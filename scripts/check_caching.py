"""Check prompt caching against the live API: two requests, the second must hit the cache.

    .venv/Scripts/python scripts/check_caching.py            # routine route (Haiku)
    .venv/Scripts/python scripts/check_caching.py coding     # coding route (Sonnet)

It builds exactly the request prefix Jarvis sends (persona system prompt plus
tool definitions, including the memory tools) and sends two one-line turns.
Expected: the first reports cache_creation_input_tokens > 0, the second
cache_read_input_tokens > 0. If both show 0, the prefix is below the model's
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
from jarvis.llm.client import AnthropicLLMClient, create_async_client
from jarvis.llm.prompts import build_system_prompt
from jarvis.llm.types import LLMRequest
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import MemoryStore
from jarvis.os_adapters.base import DefaultBrowserOpener, default_launcher
from jarvis.search.anthropic_search import AnthropicWebSearch
from jarvis.tools.registry import build_default_registry

QUESTIONS = ("Say only the word one.", "Say only the word two.")


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
        registry = build_default_registry(
            config,
            launcher=default_launcher(),
            browser=DefaultBrowserOpener(),
            search=AnthropicWebSearch(sdk, config.search),
            memory=memory,
        )
        llm = AnthropicLLMClient(sdk)
        route = config.llm.routes[route_name]
        ok = True
        try:
            for index, question in enumerate(QUESTIONS):
                request = LLMRequest(
                    route_name=route_name,
                    route=route,
                    system=build_system_prompt(config),
                    tools=registry.definitions(),
                    messages=[{"role": "user", "content": question}],
                )
                response = await llm.respond(request, _ignore)
                usage = response.usage
                usd = cost.record(response.model, usage)
                print(
                    f"request {index + 1}: model={response.model} input={usage.input_tokens} "
                    f"cache_write={usage.cache_creation_input_tokens} "
                    f"cache_read={usage.cache_read_input_tokens} cost=${usd:.6f}"
                )
                if index == 1 and usage.cache_read_input_tokens == 0:
                    ok = False
        finally:
            store.close()
            await sdk.close()
    print("caching works" if ok else "NO CACHE HIT on the second request")
    return ok


def main() -> int:
    route = sys.argv[1] if len(sys.argv) > 1 else "routine"
    return 0 if asyncio.run(check(route)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
