"""create_pdf: put a document Claude writes into a formatted PDF file.

Only when the user explicitly asks for a PDF. Claude writes the text (title plus
a small Markdown subset); :mod:`jarvis.documents.pdf_writer` lays it out and saves
it in the project's Outputs folder without ever overwriting a file.
"""

from __future__ import annotations

import asyncio
from typing import Protocol

from pydantic import Field

from jarvis.documents.pdf_writer import PdfError, SavedPdf
from jarvis.tools.base import Tool, ToolContext, ToolInput, ToolOutcome

MAX_TITLE_CHARS = 150
MAX_CONTENT_CHARS = 60_000  # schema limit; config pdf.max_content_chars may be lower


class PdfWriter(Protocol):
    def save(self, title: str, content: str, file_name: str | None) -> SavedPdf:
        """Render and save the document; raise PdfError with a user-safe message."""
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
            "lists. No tables, images, links or code blocks. Hindi must be in Devanagari."
        ),
    )
    file_name: str | None = Field(
        default=None,
        max_length=80,
        description="Optional short file name without extension, e.g. 'solar-system'.",
    )


class CreatePdfTool(Tool[CreatePdfInput]):
    name = "create_pdf"
    description = (
        "Create a PDF document and save it on the user's computer. Call this ONLY when the user "
        "explicitly asks for a PDF, for example 'make a PDF about the solar system', 'put that "
        "in a PDF' or 'iska PDF bana do'. Never use it for ordinary questions or explanations. "
        "Write the complete document yourself in the content field. Afterwards, tell the user "
        "the file name and the folder it was saved in."
    )
    input_model = CreatePdfInput

    def __init__(self, writer: PdfWriter, *, timeout_s: float) -> None:
        self._writer = writer
        self.timeout_s = timeout_s

    async def run(self, args: CreatePdfInput, ctx: ToolContext) -> ToolOutcome:
        try:
            saved = await asyncio.to_thread(
                self._writer.save, args.title, args.content, args.file_name
            )
        except PdfError as exc:
            return ToolOutcome(
                content=f"The PDF was not created: {exc}.", ok=False, summary="PDF failed"
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
                f"the J.A.R.V.I.S. project ({saved.path}). Tell the user the file name and that "
                "folder; do not read out the full path." + "".join(f" {n}" for n in notes)
            ),
            ok=True,
            summary=f"Saved PDF: {saved.folder_name}\\{saved.path.name}",
        )
