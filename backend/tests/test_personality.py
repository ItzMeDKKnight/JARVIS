"""Switchable personalities: persona structure, bands, caching layout, persistence, invariance."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.assistant import Assistant
from jarvis.config import JarvisConfig
from jarvis.events import EventBus, PersonalitySet
from jarvis.llm.client import build_request_params
from jarvis.llm.prompts import build_system_prompt, persona_sections
from jarvis.llm.types import ToolCall
from jarvis.personality import (
    BANDS,
    MODES,
    PersonaFormatError,
    Personality,
    PersonalitySettings,
    PersonalityState,
    band_of,
    personality_prompt,
    split_persona,
)
from jarvis.state import StateMachine
from jarvis.tools.base import ToolContext
from jarvis.tools.personality_tool import SetPersonalityInput, SetPersonalityTool
from jarvis.tools.registry import build_default_registry
from tests.fakes import (
    EventRecorder,
    FakeBrowser,
    FakeConfirmer,
    FakeLauncher,
    FakeLLMClient,
    FakeSearch,
    ScriptedTurn,
)
from tests.test_hud import Rig as HudRig

# -- persona.md structure ------------------------------------------------------------------


def test_shipped_persona_has_every_mode_and_band(config: JarvisConfig) -> None:
    sections = persona_sections(config)
    assert set(sections.bands) == set(MODES) - {"normal"}
    for mode, bands in sections.bands.items():
        assert tuple(sorted(bands)) == BANDS, mode
        texts = [bands[b] for b in BANDS]
        assert all(len(t) > 40 for t in texts), mode
        assert len(set(texts)) == 4, f"{mode}: each band must read differently"
    assert sections.normal


def test_the_core_keeps_every_rule_and_no_mode_text(config: JarvisConfig) -> None:
    core = build_system_prompt(config)
    for rule in (
        "Your words are spoken aloud",
        "Reply in the language",
        "Never store passwords",
        "untrusted data",
        "Never claim you did something that a tool did not actually do",
        "Personality modes change only how you talk",
        "gloats about opening Calculator, and then opens Calculator and nothing else",
    ):
        assert rule in core, rule
    assert "<!--" not in core and "supervillain" not in core


def test_malformed_persona_is_rejected() -> None:
    good = "core\n<!-- personality: normal -->\nN\n" + "".join(
        f"<!-- personality: {m} -->\n"
        + "".join(f"<!-- band: {lo}-{hi} -->\n{m} {lo} text here\n" for lo, hi in BANDS)
        for m in MODES
        if m != "normal"
    )
    assert split_persona(good).core == "core"
    with pytest.raises(PersonaFormatError):
        split_persona(good.replace("<!-- band: 76-100 -->", ""))
    with pytest.raises(PersonaFormatError):
        split_persona("no sections at all")
    with pytest.raises(PersonaFormatError):
        split_persona(good.replace("<!-- personality: lusty -->", "<!-- personality: sneaky -->"))


@pytest.mark.parametrize(
    ("intensity", "band"),
    [
        (0, (0, 25)),
        (25, (0, 25)),
        (26, (26, 50)),
        (50, (26, 50)),
        (51, (51, 75)),
        (76, (76, 100)),
        (100, (76, 100)),
    ],
)
def test_bands(intensity: int, band: tuple[int, int]) -> None:
    assert band_of(intensity) == band


def test_the_block_changes_noticeably_with_intensity(config: JarvisConfig) -> None:
    sections = persona_sections(config)
    low = personality_prompt(sections, Personality("evil", 10))
    high = personality_prompt(sections, Personality("evil", 95))
    assert low != high and "faint" in low and "supervillain" in high.casefold()
    assert "Normal" in personality_prompt(sections, Personality("normal", 90))
    for mode in MODES:
        block = personality_prompt(sections, Personality(mode, 100))
        assert "only how you talk" in block  # the invariance reminder travels with every mode


def test_intensity_is_validated() -> None:
    with pytest.raises(ValueError):
        Personality("witty", 101)
    with pytest.raises(ValueError):
        Personality("sarcastic", 50)  # type: ignore[arg-type]


# -- caching layout ------------------------------------------------------------------------


def test_core_first_personality_second_each_cached(config: JarvisConfig) -> None:
    from jarvis.llm.types import LLMRequest

    sections = persona_sections(config)
    route = config.llm.routes["routine"]

    def params(p: Personality) -> dict[str, object]:
        request = LLMRequest(
            route_name="routine",
            route=route,
            system=sections.core,
            tools=[],
            messages=[{"role": "user", "content": "hi"}],
            persona=personality_prompt(sections, p),
        )
        return build_request_params(request)

    normal, evil = params(Personality()), params(Personality("evil", 80))
    assert normal["system"][0] == evil["system"][0]  # type: ignore[index]  # same cached core
    assert normal["system"][1] != evil["system"][1]  # type: ignore[index]
    assert all(b["cache_control"] == {"type": "ephemeral"} for b in evil["system"])  # type: ignore[union-attr]


# -- persistence ---------------------------------------------------------------------------


def test_saved_and_restored(config: JarvisConfig, tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"other": 1}), encoding="utf-8")
    state = PersonalityState(persona_sections(config), settings=PersonalitySettings(path))
    assert state.current == Personality()  # nothing saved yet: normal

    import asyncio

    asyncio.run(state.set("witty", 70))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"other": 1, "personality": {"mode": "witty", "intensity": 70}}
    restored = PersonalityState(persona_sections(config), settings=PersonalitySettings(path))
    assert restored.current == Personality("witty", 70)


def test_unreadable_settings_fall_back_to_normal(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{not json", encoding="utf-8")
    assert PersonalitySettings(path).load() == Personality()
    path.write_text(
        json.dumps({"personality": {"mode": "chaotic", "intensity": 5}}), encoding="utf-8"
    )
    assert PersonalitySettings(path).load() == Personality()


# -- the voice tool and the HUD ------------------------------------------------------------


async def test_voice_tool_switches_and_keeps_intensity(
    config: JarvisConfig, tmp_path: Path
) -> None:
    bus, events = EventBus(), EventRecorder()
    bus.subscribe(events)
    state = PersonalityState(
        persona_sections(config), settings=PersonalitySettings(tmp_path / "s.json"), bus=bus
    )
    tool = SetPersonalityTool(state, timeout_s=5)
    ctx = ToolContext(bus=bus, confirmer=FakeConfirmer(True), language="hi")
    await tool.run(SetPersonalityInput(mode="joyful"), ctx)
    assert state.current == Personality("joyful", 50)  # from normal: 50 by default
    await tool.run(SetPersonalityInput(mode="evil", intensity=90), ctx)
    await tool.run(SetPersonalityInput(mode="witty"), ctx)
    assert state.current == Personality("witty", 90)  # intensity kept
    assert [(e.mode, e.intensity) for e in events.of_type("personality")] == [
        ("joyful", 50),
        ("evil", 90),
        ("witty", 90),
    ]


async def test_hud_picker_sets_it_and_connect_shows_it(
    config: JarvisConfig, tmp_path: Path
) -> None:
    rig = HudRig(config, tmp_path, [])
    state = PersonalityState(persona_sections(config), bus=rig.bus)
    rig.hud._personality = state
    await rig.hud.handle(PersonalitySet(mode="lusty", intensity=35))
    assert state.current == Personality("lusty", 35)
    snapshot = await rig.hud.snapshot()
    assert any(getattr(e, "type", "") == "personality" and e.mode == "lusty" for e in snapshot)
    rig.store.close()


# -- personality never changes what Jarvis does --------------------------------------------


def _assistant(
    config: JarvisConfig, state: PersonalityState, llm: FakeLLMClient, launcher: FakeLauncher
) -> Assistant:
    bus = EventBus()
    registry = build_default_registry(
        config,
        launcher=launcher,
        browser=FakeBrowser(),
        search=FakeSearch(),
        personality=state,
    )
    return Assistant(
        config,
        llm=llm,
        registry=registry,
        bus=bus,
        state=StateMachine(bus),
        confirmer=FakeConfirmer(True),
    )


@pytest.mark.parametrize("mode", MODES)
async def test_every_mode_opens_calculator_and_nothing_else(
    config: JarvisConfig, mode: str
) -> None:
    """Evil Jarvis gloats about opening Calculator; it still opens Calculator and nothing else.

    What the request carries is checked too: the tools and the core are byte-identical in
    every mode, so the only thing a mode can change is the small personality block.
    """
    state = PersonalityState(persona_sections(config))
    await state.set(mode, 100)  # type: ignore[arg-type]
    launcher = FakeLauncher()
    llm = FakeLLMClient(
        [
            ScriptedTurn(
                tool_calls=[ToolCall(id="t1", name="open_app", input={"app_name": "calculator"})]
            ),
            ScriptedTurn(chunks=["Calculator is open."]),
        ]
    )
    await _assistant(config, state, llm, launcher).handle_text("open calculator")
    assert [spec.target for spec in launcher.launched] == [
        "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App"
    ]
    baseline = _assistant(
        config, PersonalityState(persona_sections(config)), FakeLLMClient([]), FakeLauncher()
    )
    assert llm.requests[0].tools == baseline._tool_definitions
    assert llm.requests[0].system == build_system_prompt(config)
    assert (
        "Active personality: " + ("Normal" if mode == "normal" else mode.capitalize())
    ) in llm.requests[0].persona


async def test_a_change_applies_from_the_next_reply(config: JarvisConfig) -> None:
    state = PersonalityState(persona_sections(config))
    llm = FakeLLMClient(
        [
            ScriptedTurn(
                tool_calls=[
                    ToolCall(
                        id="p", name="set_personality", input={"mode": "evil", "intensity": 80}
                    )
                ]
            ),
            ScriptedTurn(chunks=["Excellent."]),
        ]
    )
    await _assistant(config, state, llm, FakeLauncher()).handle_text("be evil")
    assert "Normal" in llm.requests[0].persona
    assert "Evil, intensity 80%" in llm.requests[1].persona
