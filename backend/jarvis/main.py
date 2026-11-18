"""Entry point: load config, wire the components together, run the front-end.

Run with ``python -m jarvis`` (or the ``jarvis`` console script):

* default: text mode (type requests);
* ``--voice``: push-to-talk voice mode;
* ``--once "text"``: handle a single typed request and exit, which is handy
  for checking behaviour from a script;
* ``--serve``: backend for the Electron HUD (local WebSocket; started by the
  HUD with a per-launch token in ``JARVIS_HUD_TOKEN``);
* ``--export-memory FILE``: write everything Jarvis remembers to a JSON file
  and exit (no API key needed). To delete it all, use ``scripts/wipe_data.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import anthropic

from jarvis.assistant import Assistant
from jarvis.config import REPO_ROOT, ConfigError, JarvisConfig, load_api_key, load_config
from jarvis.cost import CostTracker
from jarvis.documents.designer import AnthropicDesigner
from jarvis.documents.pdf_writer import FpdfWriter
from jarvis.events import EventBus
from jarvis.hud_app import HudTokenError, read_token, run_hud
from jarvis.llm.client import AnthropicLLMClient, create_async_client
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import MemoryStore, MemoryStoreError
from jarvis.memory.summary import AnthropicConsolidator
from jarvis.os_adapters.base import DefaultBrowserOpener, default_launcher, user_data_dir
from jarvis.privacy import apply_offline_env
from jarvis.search.anthropic_search import AnthropicWebSearch
from jarvis.state import StateMachine
from jarvis.terminal import TerminalConfirmer, TerminalRenderer, run_repl
from jarvis.tools.registry import build_default_registry
from jarvis.utils.console import use_utf8_console
from jarvis.utils.logging import register_secret, setup_logging

logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. voice assistant")
    parser.add_argument("--config", type=Path, default=None, help="path to config.yaml")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--voice", action="store_true", help="push-to-talk voice mode")
    mode.add_argument("--serve", action="store_true", help="backend for the Electron HUD")
    mode.add_argument("--once", metavar="TEXT", default=None, help="handle one request and exit")
    mode.add_argument(
        "--export-memory",
        metavar="FILE",
        type=Path,
        default=None,
        help="write everything Jarvis remembers to a JSON file and exit",
    )
    return parser.parse_args(argv)


def memory_db_path(config: JarvisConfig) -> Path:
    return user_data_dir(config.paths.data_dir) / config.memory.db_file


def open_memory_store(config: JarvisConfig) -> MemoryStore:
    return MemoryStore(
        memory_db_path(config),
        max_facts=config.memory.max_facts,
        max_fact_chars=config.memory.max_fact_chars,
    )


def export_memory(config: JarvisConfig, target: Path) -> int:
    """Write the memory database to ``target`` as JSON. Returns the number of facts."""
    store = open_memory_store(config)
    try:
        data = store.export()
    finally:
        store.close()
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    facts = data["facts"]
    return len(facts) if isinstance(facts, list) else 0


@asynccontextmanager
async def _memory_service(
    config: JarvisConfig,
    sdk_client: anthropic.AsyncAnthropic,
    bus: EventBus,
    cost: CostTracker,
) -> AsyncIterator[MemoryService | None]:
    """Open memory for the session; on the way out, save a last summary and close the file."""
    if not config.memory.enabled:
        yield None
        return
    store = open_memory_store(config)
    memory = MemoryService(
        config.memory,
        store,
        AnthropicConsolidator(
            sdk_client,
            config.memory,
            user_name=config.assistant.user_name,
            assistant_name=config.assistant.name,
            cost=cost,
        ),
        user_name=config.assistant.user_name,
        assistant_name=config.assistant.name,
        history_max_turns=config.llm.history_max_turns,
    )
    bus.subscribe(memory)
    try:
        yield memory
    finally:
        try:
            await memory.flush()
        finally:
            store.close()


async def _amain(
    config: JarvisConfig, api_key: str, args: argparse.Namespace, hud_token: str | None
) -> None:
    bus = EventBus()
    if hud_token is None:
        bus.subscribe(TerminalRenderer())  # in serve mode the HUD is the only front-end
    state = StateMachine(bus)

    sdk_client = create_async_client(api_key, config.llm)
    cost = CostTracker(config.pricing, config.budget)
    try:
        async with _memory_service(config, sdk_client, bus, cost) as memory:
            search = AnthropicWebSearch(sdk_client, config.search, cost)
            registry = build_default_registry(
                config,
                launcher=default_launcher(),
                browser=DefaultBrowserOpener(),
                search=search,
                memory=memory,
                videos=search,
                pdf=FpdfWriter(config.pdf, REPO_ROOT),
                designer=(
                    AnthropicDesigner(sdk_client, config.pdf.designer, cost)
                    if config.pdf.designer.enabled
                    else None
                ),
            )
            llm = AnthropicLLMClient(sdk_client)
            if hud_token is not None:
                await run_hud(
                    config,
                    token=hud_token,
                    bus=bus,
                    state=state,
                    llm=llm,
                    registry=registry,
                    memory=memory,
                    cost=cost,
                )
                return
            if args.voice:
                # Imported lazily: text mode never loads the audio/ML stack.
                from jarvis.voice_app import VoiceStartupError, run_voice

                try:
                    await run_voice(
                        config,
                        bus=bus,
                        state=state,
                        llm=llm,
                        registry=registry,
                        memory=memory,
                        cost=cost,
                    )
                except VoiceStartupError as exc:
                    print(f"Voice mode could not start: {exc}", file=sys.stderr)
                    raise SystemExit(3) from exc
                return
            assistant = Assistant(
                config,
                llm=llm,
                registry=registry,
                bus=bus,
                state=state,
                confirmer=TerminalConfirmer(),
                memory=memory,
                cost=cost,
            )
            if args.once is not None:
                await assistant.handle_text(args.once)
            else:
                await run_repl(assistant.handle_text)
    finally:
        cost.log_session()
        await sdk_client.close()


def run(argv: list[str] | None = None) -> None:
    """Console-script entry point."""
    apply_offline_env()  # before anything can import huggingface_hub / faster-whisper
    use_utf8_console()
    args = _parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    if args.export_memory is not None:
        try:
            count = export_memory(config, args.export_memory)
        except (MemoryStoreError, OSError) as exc:
            print(f"Export failed: {exc}", file=sys.stderr)
            raise SystemExit(4) from exc
        print(f"Exported {count} facts and the conversation summary to {args.export_memory}")
        return

    try:
        api_key = load_api_key()
        hud_token = read_token(dict(os.environ)) if args.serve else None
    except (ConfigError, HudTokenError) as exc:
        print(f"Configuration problem: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    register_secret(api_key)
    if hud_token is not None:
        register_secret(hud_token)
    log_path = setup_logging(config.logging, user_data_dir(config.paths.data_dir) / "logs")
    mode = "serve" if args.serve else "voice" if args.voice else "text"
    logger.info("jarvis starting", extra={"log_path": str(log_path), "mode": mode})

    try:
        asyncio.run(_amain(config, api_key, args, hud_token))
    except MemoryStoreError as exc:
        print(f"Memory problem: {exc}", file=sys.stderr)
        raise SystemExit(4) from exc
    except KeyboardInterrupt:
        print(f"\nGoodbye, {config.assistant.user_name}.")
    logger.info("jarvis stopped")


if __name__ == "__main__":
    run()
