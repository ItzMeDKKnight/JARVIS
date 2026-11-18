"""create_pdf: layout input, Outputs folder rules, no overwriting, Hindi rendering, routing."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest

from jarvis.config import JarvisConfig
from jarvis.documents.pdf_writer import (
    MARKER_NAME,
    Block,
    FpdfWriter,
    PdfError,
    output_folder,
    parse_blocks,
    render_pdf,
    resolve_font,
    safe_stem,
    write_exclusive,
)
from jarvis.events import EventBus
from jarvis.llm.router import ModelRouter
from jarvis.tools.base import ToolContext
from jarvis.tools.pdf_tool import CreatePdfInput, CreatePdfTool
from jarvis.tools.registry import build_default_registry
from tests.fakes import FakeBrowser, FakeConfirmer, FakeLauncher, FakeSearch

NOW = datetime(2026, 11, 18, 9, 30).astimezone()


def _font_available(config: JarvisConfig) -> bool:
    try:
        resolve_font(config.pdf)
    except PdfError:
        return False
    return True


# -- content -----------------------------------------------------------------------------------


def test_markdown_subset_becomes_blocks() -> None:
    content = """# The Solar System

The **Sun** is a star.
It holds everything together.

## Planets
1. Mercury
2) Venus
- Gas giants
* Ice giants
### Moons
`Earth` has one.
"""
    assert parse_blocks(content, "The Solar System") == [
        Block("paragraph", "The Sun is a star. It holds everything together."),
        Block("heading2", "Planets"),
        Block("number", "Mercury", 1),
        Block("number", "Venus", 2),
        Block("bullet", "Gas giants"),
        Block("bullet", "Ice giants"),
        Block("heading3", "Moons"),
        Block("paragraph", "Earth has one."),
    ]


def test_a_different_first_heading_is_kept() -> None:
    blocks = parse_blocks("# Introduction\nText", "The Solar System")
    assert blocks[0] == Block("heading1", "Introduction")


@pytest.mark.parametrize(
    ("name", "stem"),
    [
        ("The Solar System!", "the-solar-system"),
        ("solar-system.pdf", "solar-system"),
        ("सौर मंडल", "सौर-मंडल"),
        ("../../Windows/System32/evil", "windows-system32-evil"),
        ('a<b>c:"d|e?*', "a-b-c-d-e"),
        ("CON", "jarvis-document-20261118-0930"),
        ("???", "jarvis-document-20261118-0930"),
    ],
)
def test_file_names_are_safe(name: str, stem: str) -> None:
    assert safe_stem(name, NOW) == stem


# -- folders and files -----------------------------------------------------------------------


def test_outputs_folder_is_created_with_a_marker(config: JarvisConfig, tmp_path: Path) -> None:
    folder, created, fallback = output_folder(tmp_path, config.pdf)
    assert folder == tmp_path / "Outputs" and created and not fallback
    assert (folder / MARKER_NAME).is_file()
    # Second time: same folder, not "created" again.
    assert output_folder(tmp_path, config.pdf) == (folder, False, False)


@pytest.mark.parametrize("foreign", ["folder", "file"])
def test_a_foreign_outputs_is_never_reused(
    config: JarvisConfig, tmp_path: Path, foreign: str
) -> None:
    existing = tmp_path / "Outputs"
    if foreign == "folder":
        existing.mkdir()
        (existing / "my-notes.txt").write_text("mine", encoding="utf-8")
    else:
        existing.write_text("not a folder", encoding="utf-8")

    folder, created, fallback = output_folder(tmp_path, config.pdf)

    assert folder == tmp_path / "Outputs_Jarvis" and created and fallback
    if foreign == "folder":
        assert sorted(p.name for p in existing.iterdir()) == ["my-notes.txt"]  # untouched
    else:
        assert existing.read_text(encoding="utf-8") == "not a folder"


def test_the_fallback_choice_sticks(config: JarvisConfig, tmp_path: Path) -> None:
    (tmp_path / "Outputs").mkdir()
    first, _, _ = output_folder(tmp_path, config.pdf)
    (tmp_path / "Outputs").rmdir()  # the foreign folder goes away later
    assert output_folder(tmp_path, config.pdf)[0] == first == tmp_path / "Outputs_Jarvis"


def test_fallback_names_are_numbered_when_taken(config: JarvisConfig, tmp_path: Path) -> None:
    (tmp_path / "Outputs").mkdir()
    (tmp_path / "Outputs_Jarvis").mkdir()
    assert output_folder(tmp_path, config.pdf)[0] == tmp_path / "Outputs_Jarvis_2"


def test_existing_pdfs_are_never_overwritten(tmp_path: Path) -> None:
    (tmp_path / "report.pdf").write_bytes(b"original")
    path, renamed = write_exclusive(tmp_path, "report", b"%PDF-new")
    assert path.name == "report-2.pdf" and renamed
    assert (tmp_path / "report.pdf").read_bytes() == b"original"
    path3, _ = write_exclusive(tmp_path, "report", b"%PDF-newer")
    assert path3.name == "report-3.pdf"
    fresh, renamed_fresh = write_exclusive(tmp_path, "other", b"%PDF")
    assert fresh.name == "other.pdf" and not renamed_fresh


# -- rendering and the tool ------------------------------------------------------------------


def test_renders_english_and_hindi(config: JarvisConfig) -> None:
    if not _font_available(config):
        pytest.skip("Nirmala UI font not available on this machine")
    blocks = [
        Block("heading2", "ग्रह"),
        Block("paragraph", "पृथ्वी तीसरा ग्रह है, और बृहस्पति सबसे बड़ा। Mixed English text."),
        Block("bullet", "क्षुद्रग्रह और धूमकेतु"),
        Block("number", "Mercury", 1),
    ]
    data = render_pdf("सौर मंडल", blocks, config.pdf, NOW)
    assert data.startswith(b"%PDF-")
    assert b"/Lang (hi)" in data
    assert b"NirmalaUI" in data or b"Nirmala" in data  # the Devanagari font is embedded


def test_missing_font_is_reported_clearly(config: JarvisConfig) -> None:
    pdf_config = config.pdf.model_copy(update={"font_file": "C:/nope/missing.ttf"})
    with pytest.raises(PdfError, match=r"pdf\.font_file"):
        render_pdf("T", [Block("paragraph", "x")], pdf_config, NOW)


def _ctx() -> ToolContext:
    return ToolContext(bus=EventBus(), confirmer=FakeConfirmer(True), language="en")


async def test_tool_saves_and_reports_name_folder_and_renames(
    config: JarvisConfig, tmp_path: Path
) -> None:
    if not _font_available(config):
        pytest.skip("Nirmala UI font not available on this machine")
    tool = CreatePdfTool(FpdfWriter(config.pdf, tmp_path), timeout_s=20)
    args = CreatePdfInput(
        title="The Solar System", content="## Planets\n- Earth\n- Mars", file_name="solar-system"
    )

    first = await tool.run(args, _ctx())
    second = await tool.run(args, _ctx())

    assert first.ok and second.ok
    assert "'solar-system.pdf'" in first.content and "'Outputs'" in first.content
    assert "'solar-system-2.pdf'" in second.content and "got a number" in second.content
    assert (tmp_path / "Outputs" / "solar-system.pdf").read_bytes().startswith(b"%PDF-")


async def test_tool_mentions_the_fallback_folder(config: JarvisConfig, tmp_path: Path) -> None:
    if not _font_available(config):
        pytest.skip("Nirmala UI font not available on this machine")
    (tmp_path / "Outputs").mkdir()
    tool = CreatePdfTool(FpdfWriter(config.pdf, tmp_path), timeout_s=20)
    outcome = await tool.run(CreatePdfInput(title="Notes", content="Hello."), _ctx())
    assert outcome.ok and "'Outputs_Jarvis'" in outcome.content
    assert "did not create" in outcome.content


async def test_tool_reports_errors_without_raising(config: JarvisConfig, tmp_path: Path) -> None:
    tool = CreatePdfTool(FpdfWriter(config.pdf, tmp_path), timeout_s=20)
    outcome = await tool.run(CreatePdfInput(title="Empty", content="   \n  "), _ctx())
    assert not outcome.ok and "no content" in outcome.content
    assert not os.path.exists(tmp_path / "Outputs")  # nothing created for a failed PDF


def test_registered_only_with_a_writer(config: JarvisConfig, tmp_path: Path) -> None:
    def names(**extra: object) -> list[str]:
        return build_default_registry(
            config,
            launcher=FakeLauncher(),
            browser=FakeBrowser(),
            search=FakeSearch(),
            **extra,  # type: ignore[arg-type]
        ).names

    assert "create_pdf" not in names()
    assert "create_pdf" in names(pdf=FpdfWriter(config.pdf, tmp_path))
    assert "create_pdf" in config.narration


@pytest.mark.parametrize(
    ("text", "route"),
    [
        ("make a PDF about the solar system", "document"),
        ("put that in a pdf", "document"),
        ("सौर मंडल पर एक पीडीएफ बनाओ", "document"),
        ("what is the solar system", "routine"),
        ("write a python function for binary search", "coding"),
    ],
)
def test_pdf_requests_get_room_for_the_whole_document(
    config: JarvisConfig, text: str, route: str
) -> None:
    name, chosen = ModelRouter(config.llm).route(text)
    assert name == route
    if route == "document":
        assert chosen.max_tokens >= 8000
