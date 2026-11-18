"""Designed PDFs: the untrusted design spec, the Haiku designer call, rendering, fallback."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pydantic
import pytest

from jarvis.config import JarvisConfig
from jarvis.cost import CostTracker
from jarvis.documents.design import (
    THEMES,
    DesignBlock,
    DesignRejectedError,
    DocumentDesign,
    KeyFigure,
    SafeDesign,
    clean,
    design_json_schema,
    sanitize,
)
from jarvis.documents.designed_renderer import render_designed
from jarvis.documents.designer import AnthropicDesigner, DesignError
from jarvis.documents.pdf_writer import FpdfWriter, PdfError, resolve_font
from jarvis.events import EventBus
from jarvis.tools.base import ToolContext
from jarvis.tools.pdf_tool import CreatePdfInput, CreatePdfTool
from tests.fakes import EventRecorder, FakeConfirmer

NOW = datetime(2026, 11, 18, 9, 30).astimezone()
SOURCE = (
    "## Planets\nMercury is 0.39 AU from the Sun, Venus 0.72 AU, Earth 1 AU and Mars 1.52 AU.\n\n"
    "Jupiter has 95 moons and Saturn has 146.\n\n"
    "सौर मंडल में आठ ग्रह हैं और बृहस्पति सबसे बड़ा ग्रह है।"
)


def _font(config: JarvisConfig) -> bool:
    try:
        resolve_font(config.pdf)
    except PdfError:
        return False
    return True


def _design(**overrides: Any) -> DocumentDesign:
    blocks = [
        DesignBlock(kind="heading", level=1, text="Planets"),
        DesignBlock(
            kind="paragraph",
            text="Mercury is 0.39 AU from the Sun, Venus 0.72 AU, Earth 1 AU and Mars 1.52 AU.",
        ),
        DesignBlock(
            kind="chart",
            chart_type="bar",
            title="Distance",
            unit="AU",
            labels=["Mercury", "Venus", "Earth", "Mars"],
            values=[0.39, 0.72, 1, 1.52],
        ),
        DesignBlock(kind="callout", style="tip", text="Jupiter has 95 moons and Saturn has 146."),
        DesignBlock(
            kind="table",
            columns=["Planet", "Moons"],
            rows=[["Jupiter", "95"], ["Saturn", "146"]],
        ),
        DesignBlock(kind="paragraph", text="सौर मंडल में आठ ग्रह हैं और बृहस्पति सबसे बड़ा ग्रह है।"),
    ]
    fields: dict[str, Any] = {
        "theme": "dark_blue",
        "cover": False,
        "title": "The Solar System",
        "blocks": blocks,
    }
    fields.update(overrides)
    return DocumentDesign(**fields)


# -- sanitising the untrusted design --------------------------------------------------------


def test_a_good_design_is_kept() -> None:
    safe = sanitize(_design(), SOURCE, "The Solar System")
    assert safe.theme == "dark_blue"
    assert [b.kind for b in safe.blocks] == [
        "heading",
        "paragraph",
        "chart",
        "callout",
        "table",
        "paragraph",
    ]


def test_invented_chart_numbers_drop_the_chart_but_keep_the_text() -> None:
    design = _design()
    design.blocks[2] = design.blocks[2].model_copy(update={"values": [0.39, 0.72, 1, 9.99]})
    safe = sanitize(design, SOURCE, "The Solar System")
    assert "chart" not in [b.kind for b in safe.blocks]
    nan = _design()
    nan.blocks[2] = nan.blocks[2].model_copy(update={"values": [float("nan"), 0.72, 1, 1.52]})
    assert "chart" not in [b.kind for b in sanitize(nan, SOURCE, "T").blocks]


def test_a_design_that_drops_the_content_is_rejected() -> None:
    thin = _design(blocks=[DesignBlock(kind="heading", level=1, text="Planets")])
    with pytest.raises(DesignRejectedError):
        sanitize(thin, SOURCE, "The Solar System")
    short = _design(blocks=[DesignBlock(kind="paragraph", text="Planets exist.")])
    with pytest.raises(DesignRejectedError):
        sanitize(short, SOURCE, "The Solar System")


def test_text_is_cleaned_and_bounded() -> None:
    assert clean("a\x00b‮c  **bold**\n\nline", 100) == "abc bold line"
    assert len(clean("x" * 9000, 50)) == 50
    design = _design()
    design.blocks.append(
        DesignBlock(kind="table", columns=[f"c{i}" for i in range(20)], rows=[["1"] * 30] * 99)
    )
    design.blocks.append(DesignBlock(kind="bullets", items=["item"] * 500))
    design.blocks.append(DesignBlock(kind="heading", level=7, text="Odd level"))
    safe = sanitize(design, SOURCE, "T")
    table = [b for b in safe.blocks if b.kind == "table"][-1]
    assert len(table.columns) == 6 and len(table.rows) == 40
    assert all(len(row) == 6 for row in table.rows)
    assert len(next(b for b in safe.blocks if b.kind == "bullets").items) == 40
    assert next(b for b in safe.blocks if b.text == "Odd level").level == 2


def test_only_known_themes_icons_and_styles_parse() -> None:
    with pytest.raises(pydantic.ValidationError):
        _design(theme="hacker_green")
    with pytest.raises(pydantic.ValidationError):
        DesignBlock(kind="callout", text="x", icon="skull")
    with pytest.raises(pydantic.ValidationError):
        DesignBlock(kind="script", text="alert(1)")


# -- the designer call ----------------------------------------------------------------------


class FakeMessages:
    def __init__(self, response: Any = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.response


def _response(design: DocumentDesign | str | None, stop_reason: str = "end_turn") -> Any:
    usage = SimpleNamespace(
        input_tokens=500,
        output_tokens=800,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=0,
    )
    text = design.model_dump_json() if isinstance(design, DocumentDesign) else (design or "{}")
    return SimpleNamespace(
        content=[SimpleNamespace(type="text", text=text)],
        stop_reason=stop_reason,
        usage=usage,
        model="claude-haiku-5-5",
    )


class FakeClient:
    def __init__(self, messages: FakeMessages) -> None:
        self.messages = messages
        self.options: list[dict[str, Any]] = []

    def with_options(self, **options: Any) -> FakeClient:
        self.options.append(options)
        return self


def _designer(config: JarvisConfig, messages: FakeMessages, cost: CostTracker | None = None) -> Any:
    client = FakeClient(messages)
    return AnthropicDesigner(client, config.pdf.designer, cost)  # type: ignore[arg-type]


async def test_designer_uses_the_configured_model_and_records_cost(config: JarvisConfig) -> None:
    messages = FakeMessages(_response(_design(theme="warm")))
    cost = CostTracker(config.pricing, config.budget)
    safe = await _designer(config, messages, cost).design("The Solar System", SOURCE, None)
    call = messages.calls[0]
    assert call["model"] == config.pdf.designer.model
    schema = call["output_config"]["format"]["schema"]
    assert schema == design_json_schema() and "anyOf" not in str(schema)
    assert "<document>" in call["messages"][0]["content"]
    assert safe.theme == "warm"
    assert cost.session_usd > 0


async def test_a_requested_theme_always_wins(config: JarvisConfig) -> None:
    messages = FakeMessages(_response(_design(theme="minimal")))
    safe = await _designer(config, messages).design("T", SOURCE, "formal_report")
    assert safe.theme == "formal_report"
    assert "Requested theme: formal_report" in messages.calls[0]["messages"][0]["content"]


async def test_the_budget_is_checked_before_calling(config: JarvisConfig) -> None:
    messages = FakeMessages(_response(_design()))
    budget = config.budget.model_copy(update={"session_cap_usd": 0.000001})
    cost = CostTracker(config.pricing, budget)
    with pytest.raises(DesignError, match="spending limit"):
        await _designer(config, messages, cost).design("T", SOURCE, None)
    assert messages.calls == []  # never sent


@pytest.mark.parametrize(
    ("messages", "match"),
    [
        (FakeMessages(_response(None)), "invalid"),
        (FakeMessages(_response(_design(), "refusal")), "declined"),
        (FakeMessages(_response(_design(), "max_tokens")), "too long"),
        (FakeMessages(_response('{"theme": "classic", "cover": false}')), "invalid"),
        (
            FakeMessages(
                _response(
                    '{"theme": "neon", "cover": false, "title": "t", "subtitle": "", "blocks": []}'
                )
            ),
            "invalid",
        ),
        (
            FakeMessages(_response(_design(blocks=[DesignBlock(kind="paragraph", text="Tiny.")]))),
            "left out",
        ),
    ],
)
async def test_designer_failures_become_design_errors(
    config: JarvisConfig, messages: FakeMessages, match: str
) -> None:
    with pytest.raises(DesignError, match=match):
        await _designer(config, messages).design("T", SOURCE, None)


# -- rendering and the tool -----------------------------------------------------------------


@pytest.mark.parametrize("theme", sorted(THEMES))
@pytest.mark.parametrize("cover", [False, True])
def test_every_theme_renders(config: JarvisConfig, theme: str, cover: bool) -> None:
    if not _font(config):
        pytest.skip("Nirmala UI font not available")
    design = _design(theme=theme, cover=cover, subtitle="A guide / एक मार्गदर्शिका")
    design.blocks.append(
        DesignBlock(
            kind="key_figures",
            figures=[KeyFigure(value="95", label="moons"), KeyFigure(value="146", label="moons")],
        )
    )
    design.blocks.append(
        DesignBlock(kind="chart", chart_type="pie", labels=["J", "S"], values=[95, 146])
    )
    design.blocks.append(
        DesignBlock(kind="chart", chart_type="line", labels=["a", "b", "c"], values=[0.39, 1, 1.52])
    )
    design.blocks.append(DesignBlock(kind="divider"))
    for icon in ("info", "tip", "warning", "check", "star", "clock", "flag", "bolt"):
        design.blocks.append(DesignBlock(kind="callout", icon=icon, text="Jupiter has 95 moons."))
    data = render_designed(sanitize(design, SOURCE, "T"), config.pdf, NOW)
    assert data.startswith(b"%PDF-") and len(data) > 5000


class FakeDesigner:
    def __init__(self, result: SafeDesign | None = None, error: str | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, str, str | None]] = []

    async def design(self, title: str, content: str, theme: Any) -> SafeDesign:
        self.calls.append((title, content, theme))
        if self.error is not None:
            raise DesignError(self.error)
        assert self.result is not None
        return self.result


def _ctx() -> tuple[ToolContext, EventRecorder]:
    bus, events = EventBus(), EventRecorder()
    bus.subscribe(events)
    return ToolContext(bus=bus, confirmer=FakeConfirmer(True), language="en"), events


async def test_tool_makes_a_designed_pdf_and_shows_it(config: JarvisConfig, tmp_path: Path) -> None:
    if not _font(config):
        pytest.skip("Nirmala UI font not available")
    designer = FakeDesigner(sanitize(_design(), SOURCE, "The Solar System"))
    tool = CreatePdfTool(FpdfWriter(config.pdf, tmp_path), timeout_s=30, designer=designer)
    ctx, events = _ctx()
    args = CreatePdfInput(title="The Solar System", content=SOURCE, theme="dark_blue")
    outcome = await tool.run(args, ctx)
    assert outcome.ok and "Dark blue style" in outcome.content
    assert designer.calls == [("The Solar System", SOURCE, "dark_blue")]
    saved = events.of_type("file_saved")[0]
    assert saved.name == "the-solar-system.pdf" and saved.folder == "Outputs"
    assert Path(saved.path).read_bytes().startswith(b"%PDF-")


async def test_tool_falls_back_to_the_plain_pdf_and_says_so(
    config: JarvisConfig, tmp_path: Path
) -> None:
    if not _font(config):
        pytest.skip("Nirmala UI font not available")
    designer = FakeDesigner(error="the design step timed out")
    tool = CreatePdfTool(FpdfWriter(config.pdf, tmp_path), timeout_s=30, designer=designer)
    ctx, events = _ctx()
    outcome = await tool.run(CreatePdfInput(title="Notes", content=SOURCE), ctx)
    assert outcome.ok
    assert "plain version" in outcome.content and "timed out" in outcome.content
    assert len(events.of_type("file_saved")) == 1


def test_theme_is_an_enum_in_the_tool_schema() -> None:
    schema = CreatePdfInput.model_json_schema()
    theme = schema["properties"]["theme"]
    enum = next(option["enum"] for option in theme["anyOf"] if "enum" in option)
    assert set(enum) == set(THEMES)


def test_schema_requires_every_field_and_keeps_title_properties() -> None:
    schema = design_json_schema()
    assert "title" in schema["properties"] and set(schema["required"]) == set(schema["properties"])
    block = schema["properties"]["blocks"]["items"]
    assert set(block["required"]) == set(block["properties"])
    assert "$ref" not in str(schema) and "anyOf" not in str(schema)
