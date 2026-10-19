"""remember / recall_memory / forget_memory through the registry, as Claude would call them."""

from __future__ import annotations

from pathlib import Path

import pytest

from jarvis.config import JarvisConfig
from jarvis.events import EventBus
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import MemoryStore
from jarvis.tools.base import ToolContext, ToolOutcome
from jarvis.tools.registry import ToolRegistry, build_default_registry
from tests.fakes import FakeBrowser, FakeConfirmer, FakeLauncher, FakeSearch


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    s = MemoryStore(tmp_path / "memory.db", max_facts=50, max_fact_chars=300)
    yield s
    s.close()


@pytest.fixture
def registry(config: JarvisConfig, store: MemoryStore) -> ToolRegistry:
    memory = MemoryService(
        config.memory,
        store,
        None,
        user_name="Tony",
        assistant_name="JARVIS",
        history_max_turns=20,
    )
    return build_default_registry(
        config, launcher=FakeLauncher(), browser=FakeBrowser(), search=FakeSearch(), memory=memory
    )


async def _call(
    registry: ToolRegistry, name: str, args: dict[str, object], confirm: bool = True
) -> tuple[ToolOutcome, FakeConfirmer]:
    confirmer = FakeConfirmer(confirm)
    ctx = ToolContext(bus=EventBus(), confirmer=confirmer, language="en")
    return await registry.execute("c1", name, args, ctx), confirmer


def _seed(store: MemoryStore, *texts: str) -> None:
    for text in texts:
        store.add_fact(text, category="other", keywords=(), source="explicit")


def test_memory_tools_are_registered_and_sorted(registry: ToolRegistry) -> None:
    names = [d["name"] for d in registry.definitions()]
    assert {"remember", "recall_memory", "forget_memory"} <= set(names)
    assert names == sorted(names)


def test_memory_tools_absent_when_memory_disabled(config: JarvisConfig) -> None:
    registry = build_default_registry(
        config, launcher=FakeLauncher(), browser=FakeBrowser(), search=FakeSearch()
    )
    assert "remember" not in registry.names


async def test_remember_stores_fact(registry: ToolRegistry, store: MemoryStore) -> None:
    outcome, _ = await _call(
        registry, "remember", {"fact": "Tony's sister is called Meera.", "keywords": ["बहन"]}
    )
    assert outcome.ok and outcome.content == "Remembered."
    assert store.facts()[0].keywords == ("बहन",)
    again, _ = await _call(registry, "remember", {"fact": "Tony's sister is called Meera."})
    assert again.content == "Already remembered."


async def test_remember_refuses_sensitive_data(registry: ToolRegistry, store: MemoryStore) -> None:
    outcome, _ = await _call(registry, "remember", {"fact": "Tony's bank PIN is 4821."})
    assert not outcome.ok
    assert outcome.content.startswith("BLOCKED")
    assert "4821" not in outcome.content
    assert store.facts() == []


async def test_recall_lists_matches_or_says_nothing(
    registry: ToolRegistry, store: MemoryStore
) -> None:
    _seed(store, "Tony's sister is called Meera.", "Tony plays football.")
    outcome, _ = await _call(registry, "recall_memory", {"query": "sister"})
    assert outcome.content == "Stored notes:\n- Tony's sister is called Meera."
    everything, _ = await _call(registry, "recall_memory", {})
    assert "football" in everything.content and "Meera" in everything.content
    nothing, _ = await _call(registry, "recall_memory", {"query": "cars"})
    assert nothing.content == "No stored notes about 'cars'."
    assert not nothing.untrusted


async def test_forget_single_match_needs_no_confirmation(
    registry: ToolRegistry, store: MemoryStore
) -> None:
    _seed(store, "Tony's sister is called Meera.", "Tony plays football.")
    outcome, confirmer = await _call(registry, "forget_memory", {"query": "my sister"})
    assert outcome.ok and outcome.summary == "Forgot 1 notes"
    assert confirmer.questions == []
    assert [f.text for f in store.facts()] == ["Tony plays football."]


async def test_forget_several_asks_first(registry: ToolRegistry, store: MemoryStore) -> None:
    _seed(store, "Tony's sister is called Meera.", "Tony's sister studies medicine.")
    declined, confirmer = await _call(registry, "forget_memory", {"query": "sister"}, confirm=False)
    assert confirmer.questions == ["I have 2 notes about sister. Shall I forget all of them?"]
    assert declined.summary == "Forget declined"
    assert len(store.facts()) == 2

    accepted, _ = await _call(registry, "forget_memory", {"query": "sister"}, confirm=True)
    assert accepted.summary == "Forgot 2 notes"
    assert store.facts() == []


async def test_forget_everything_needs_yes(registry: ToolRegistry, store: MemoryStore) -> None:
    _seed(store, "Tony likes chess.")
    no, _ = await _call(registry, "forget_memory", {"everything": True}, confirm=False)
    assert no.summary == "Wipe declined" and store.facts()
    yes, confirmer = await _call(registry, "forget_memory", {"everything": True}, confirm=True)
    assert yes.summary == "Memory erased" and store.facts() == []
    assert confirmer.questions == ["This erases everything I remember about you. Are you sure?"]


async def test_forget_without_query_asks_model_to_clarify(registry: ToolRegistry) -> None:
    outcome, _ = await _call(registry, "forget_memory", {})
    assert not outcome.ok and "Ask the user" in outcome.content


async def test_memory_tools_have_no_narration(registry: ToolRegistry) -> None:
    bus = EventBus()
    started: list[str] = []

    async def record(event: object) -> None:
        if getattr(event, "type", None) == "tool_started":
            started.append(event.summary)  # type: ignore[attr-defined]

    bus.subscribe(record)  # type: ignore[arg-type]
    ctx = ToolContext(bus=bus, confirmer=FakeConfirmer(True), language="en")
    await registry.execute("c1", "recall_memory", {}, ctx)
    assert started == [""]
