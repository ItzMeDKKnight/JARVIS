"""create_pdf: turn a document Claude writes into a designed PDF file.

Only when the user explicitly asks for a PDF. Three steps, three owners:

1. the main conversation decides what the document *says* (title plus simple Markdown);
2. a separate small Claude call, the designer (:mod:`jarvis.documents.designer`), decides
   how it *looks*: theme, cover, sections, callouts, tables, charts;
3. :mod:`jarvis.documents.designed_renderer` draws the sanitised design on this machine.

If the design step is off, fails, returns something unusable, or the session budget can't
cover it, the plain layout (:mod:`jarvis.documents.pdf_writer`) is used instead and the
result says so, so Jarvis can tell the user. Either way the file lands in the project's
Outputs folder without ever overwriting a file, and a ``file_saved`` event lets the HUD
show it with an Open button.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, get_args

from pydantic import Field

from jarvis.documents.design import THEMES, SafeDesign, ThemeName
from jarvis.documents.designer import Designer, DesignError
from jarvis.documents.pdf_writer import PdfError, SavedPdf
from jarvis.events import FileSaved
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome

logger = logging.getLogger(__name__)

MAX_TITLE_CHARS = 150
MAX_CONTENT_CHARS = 60_000  # schema limit; config pdf.max_content_chars may be lower
THEME_NAMES: tuple[str, ...] = get_args(ThemeName)


class PdfWriter(Protocol):
    def save(self, title: str, content: str, file_name: str | None) -> SavedPdf:
        """Render the plain layout and save it; raise PdfError with a user-safe message."""
        ...

    def save_designed(self, design: SafeDesign, file_name: str | None) -> SavedPdf:
        """Render a sanitised design and save it; raise PdfError if it can't be drawn."""
        ...


class CreatePdfInput(ToolInput):
    title: str = Field(
        min_length=1,
        max_length=MAX_TITLE_CHARS,
        description="Document title, in the language of the document.",
    )
    content: str = Field(
        min_length=1,
        max_length=MAX_CONTENT_CHARS,
        description=(
            "The full document body in simple Markdown: '## Heading' and '### Subheading' lines, "
            "paragraphs separated by blank lines, '- item' bullet lists and '1. item' numbered "
            "lists. Include the actual facts and numbers; tables and charts are made from them. "
            "No images, links or code blocks. Hindi must be in Devanagari."
        ),
    )
    file_name: str | None = Field(
        default=None,
        max_length=80,
        description="Optional short file name without extension, e.g. 'solar-system'.",
    )
    theme: ThemeName | None = Field(
        default=None,
        description=(
            "Visual theme, only if the user asked for a look: "
            + "; ".join(f"{name} = {t.label}" for name, t in THEMES.items())
            + ". 'dark blue' -> dark_blue, 'formal report style' -> formal_report, "
            "'simple' or 'clean' -> minimal. Omit it to let the designer choose."
        ),
    )


class CreatePdfTool(Tool[CreatePdfInput]):
    name = "create_pdf"
    description = (
        "Create a designed PDF document and save it on the user's computer. Call this ONLY "
        "when the user explicitly asks for a PDF, for example 'make a PDF about the solar "
        "system', 'put that in a PDF', 'iska PDF bana do', or to restyle a PDF you just made "
        "('make it dark blue', 'formal report style': call it again with the same content and "
        "the theme). Never use it for ordinary questions or explanations. Write the complete "
        "document yourself in the content field. Afterwards, tell the user the file name and "
        "the folder it was saved in."
    )
    input_model = CreatePdfInput

    def __init__(
        self, writer: PdfWriter, *, timeout_s: float, designer: Designer | None = None
    ) -> None:
        self._writer = writer
        self._designer = designer
        self.timeout_s = timeout_s

    async def run(self, args: CreatePdfInput, ctx: ToolContext) -> ToolOutcome:
        saved: SavedPdf | None = None
        design_note = ""
        if self._designer is not None:
            try:
                design = await self._designer.design(args.title, args.content, args.theme)
                saved = await asyncio.to_thread(self._writer.save_designed, design, args.file_name)
                theme_label = THEMES[design.theme].label
            except (DesignError, PdfError) as exc:
                logger.warning(
                    "designed pdf failed; using the plain layout", extra={"why": str(exc)}
                )
                design_note = (
                    f" The designed layout didn't work ({exc}), so this is the plain version. "
                    "Tell the user that in one short sentence."
                )
        if saved is None:
            try:
                saved = await asyncio.to_thread(
                    self._writer.save, args.title, args.content, args.file_name
                )
            except PdfError as exc:
                return ToolOutcome(
                    content=f"The PDF was not created: {exc}.", ok=False, summary="PDF failed"
                )
            theme_label = "plain"

        await ctx.bus.publish(
            FileSaved(
                path=str(saved.path.resolve()),
                name=saved.path.name,
                folder=saved.folder_name,
                kind="pdf",
            )
        )
        notes = []
        if saved.fallback_folder:
            notes.append(
                "The project already contains an item named 'Outputs' that Jarvis did not "
                f"create, so it was left untouched and PDFs are saved in '{saved.folder_name}' "
                "instead. Mention this folder name."
            )
        if saved.renamed:
            notes.append(
                "A file with the requested name already existed, so this one got a number "
                "instead of replacing it. Mention that."
            )
        return ToolOutcome(
            content=(
                f"Saved the PDF as '{saved.path.name}' in the '{saved.folder_name}' folder of "
                f"the J.A.R.V.I.S. project ({saved.path}), {theme_label} style. Tell the user the "
                "file name and that folder; do not read out the full path. It is also shown on "
                "screen with an Open button." + "".join(f" {n}" for n in notes) + design_note
            ),
            ok=True,
            summary=f"Saved PDF: {saved.folder_name}\\{saved.path.name}",
        )
