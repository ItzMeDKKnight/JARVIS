"""Turns a title plus simple Markdown into a formatted PDF, saved without overwriting anything.

Claude writes the content; this module only lays it out. The input format is a
small Markdown subset that language models write reliably:

    # / ## / ###     headings (a leading "# Title" equal to the title is dropped)
    - item / * item  bullet list
    1. item          numbered list
    blank line       paragraph break
    **bold**         markers are removed (Nirmala UI has no italic, so no inline styles)

Rendering uses fpdf2 with HarfBuzz text shaping (uharfbuzz), so Devanagari
matras and conjuncts come out right, and Windows' own Nirmala UI font, which
covers both Latin and Devanagari. fpdf2 can fetch remote images, but this module
never asks it to: only text is ever placed, so writing a PDF makes no network calls.

Where files go (the brief): ``<project>/Outputs``. A marker file records that
Jarvis created the folder. If something named ``Outputs`` already exists without
the marker, it is left alone and ``Outputs_Jarvis`` (then ``Outputs_Jarvis_2`` ...)
is used; once a folder carries the marker it is reused from then on. Files are
created with exclusive-create mode, so an existing PDF is never overwritten: a
taken name gets a number (``solar-system-2.pdf``).
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from jarvis.config import PdfConfig
from jarvis.documents.design import SafeDesign

MARKER_NAME = ".jarvis-outputs"
MARKER_TEXT = (
    "This folder was created by J.A.R.V.I.S. for files it generates (PDFs).\n"
    "It is git-ignored. You can delete it; Jarvis recreates it when needed.\n"
)
MAX_NUMBERED_COPIES = 999
MAX_STEM_CHARS = 80
_FALLBACK_SCAN = 50  # Outputs_Jarvis, Outputs_Jarvis_2 ... _50

BlockKind = Literal["heading1", "heading2", "heading3", "paragraph", "bullet", "number"]


class PdfError(Exception):
    """The PDF could not be written; the message is safe to show to the user."""


@dataclass(frozen=True)
class Block:
    kind: BlockKind
    text: str
    number: int = 0  # for numbered items


@dataclass(frozen=True)
class SavedPdf:
    path: Path
    folder_name: str
    folder_created: bool  # True the first time the folder was made
    fallback_folder: bool  # True when "Outputs" was taken by something else
    renamed: bool  # True when the requested file name was taken


# -- content --------------------------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*(\d{1,3})[.)]\s+(.*)$")
_INLINE_MARKERS = re.compile(r"(\*\*|__|`)")


def _clean_inline(text: str) -> str:
    return " ".join(_INLINE_MARKERS.sub("", text).split())


def parse_blocks(content: str, title: str) -> list[Block]:
    """Split the Markdown subset into blocks. Unknown syntax is kept as plain text."""
    blocks: list[Block] = []
    paragraph: list[str] = []

    def flush() -> None:
        if paragraph:
            blocks.append(Block("paragraph", _clean_inline(" ".join(paragraph))))
            paragraph.clear()

    for raw_line in content.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if heading := _HEADING.match(line):
            flush()
            level = min(len(heading.group(1)), 3)
            kind: BlockKind = "heading1" if level == 1 else "heading2" if level == 2 else "heading3"
            blocks.append(Block(kind, _clean_inline(heading.group(2))))
        elif bullet := _BULLET.match(line):
            flush()
            blocks.append(Block("bullet", _clean_inline(bullet.group(1))))
        elif numbered := _NUMBERED.match(line):
            flush()
            blocks.append(Block("number", _clean_inline(numbered.group(2)), int(numbered.group(1))))
        else:
            paragraph.append(line)
    flush()
    blocks = [block for block in blocks if block.text]
    # Models often repeat the title as "# Title"; it is already printed as the title.
    if blocks and blocks[0].kind == "heading1" and _same(blocks[0].text, title):
        blocks = blocks[1:]
    return blocks


def _same(a: str, b: str) -> bool:
    return " ".join(a.casefold().split()) == " ".join(b.casefold().split())


# -- file names and folders -----------------------------------------------------------------

_UNSAFE_CHARS = re.compile(r"[^\wऀ-ॿ\-]+")
_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


def safe_stem(name: str, now: datetime) -> str:
    """A Windows-safe file name (no extension) from a title or a requested name."""
    text = unicodedata.normalize("NFC", name).strip()
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)
    stem = _UNSAFE_CHARS.sub("-", text).strip("-_.")
    stem = re.sub(r"-{2,}", "-", stem)[:MAX_STEM_CHARS].strip("-_.").lower()
    if not stem or stem in _RESERVED:
        stem = f"jarvis-document-{now:%Y%m%d-%H%M}"
    return stem


def _is_ours(folder: Path) -> bool:
    return (
        folder.is_dir()
        and not folder.is_symlink()
        and not _is_junction(folder)
        and (folder / MARKER_NAME).is_file()
    )


def _is_junction(folder: Path) -> bool:
    is_junction = getattr(folder, "is_junction", None)  # Python 3.12+
    return bool(is_junction()) if callable(is_junction) else False


def _candidates(config: PdfConfig) -> list[str]:
    names = [config.output_dir_name, config.fallback_dir_name]
    names.extend(f"{config.fallback_dir_name}_{n}" for n in range(2, _FALLBACK_SCAN + 1))
    return names


def output_folder(root: Path, config: PdfConfig) -> tuple[Path, bool, bool]:
    """(folder, created_now, is_fallback). Never reuses a folder Jarvis didn't create."""
    names = _candidates(config)
    for name in names:  # a folder we made earlier wins, so the choice sticks
        if _is_ours(root / name):
            return root / name, False, name != config.output_dir_name
    for name in names:
        folder = root / name
        if os.path.lexists(folder):
            continue  # a file, folder or link that isn't ours: leave it alone
        try:
            folder.mkdir()
            (folder / MARKER_NAME).write_text(MARKER_TEXT, encoding="utf-8")
        except FileExistsError:
            continue  # created by someone else just now
        except OSError as exc:
            raise PdfError(f"could not create the {name} folder: {exc}") from exc
        return folder, True, name != config.output_dir_name
    raise PdfError("no free folder name for PDFs in the project")


def write_exclusive(folder: Path, stem: str, data: bytes) -> tuple[Path, bool]:
    """Write ``data`` as ``stem.pdf`` or ``stem-2.pdf`` ...; never replaces an existing file."""
    for n in range(1, MAX_NUMBERED_COPIES + 1):
        path = folder / (f"{stem}.pdf" if n == 1 else f"{stem}-{n}.pdf")
        try:
            with path.open("xb") as handle:  # exclusive create: fails if the name exists
                handle.write(data)
        except FileExistsError:
            continue
        except OSError as exc:
            raise PdfError(f"could not save the PDF: {exc}") from exc
        return path, n > 1
    raise PdfError("too many PDFs with that name already")


# -- rendering ------------------------------------------------------------------------------

_FAMILY = "JarvisSans"
_SIZES = {"title": 22.0, "heading1": 16.0, "heading2": 13.5, "heading3": 12.0, "body": 11.0}
_LIST_INDENT_MM = 6.0
_LINE = 1.45  # line height as a multiple of the font size (Devanagari needs room)
_PT_TO_MM = 0.3528


def _has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def resolve_font(config: PdfConfig) -> Path:
    path = Path(os.path.expandvars(config.font_file))
    if not path.is_file():
        raise PdfError(
            f"the PDF font {path} was not found; set pdf.font_file in config.yaml to a "
            "font with Devanagari (for example Nirmala UI or Noto Sans Devanagari)"
        )
    return path


def render_pdf(title: str, blocks: list[Block], config: PdfConfig, now: datetime) -> bytes:
    """Lay out the title and blocks on A4 pages and return the PDF bytes."""
    from fpdf import FPDF  # imported here: only needed when a PDF is actually made
    from fpdf.enums import XPos, YPos

    # fontTools reports OpenType tables it drops while subsetting (harmless "MERG" notes).
    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
    font = resolve_font(config)
    pdf = FPDF(format="A4", unit="mm")
    pdf.set_margins(20, 20, 20)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font(_FAMILY, "", font, collection_font_number=config.font_index_regular)
    pdf.add_font(_FAMILY, "B", font, collection_font_number=config.font_index_bold)
    pdf.set_text_shaping(True)  # HarfBuzz: correct Devanagari matras and conjuncts
    pdf.set_title(title)
    pdf.set_author("J.A.R.V.I.S.")
    pdf.set_creator("J.A.R.V.I.S.")
    pdf.set_creation_date(now)
    pdf.set_lang("hi" if _has_devanagari(title) else "en")
    pdf.add_page()

    def write(text: str, size: float, *, bold: bool = False, indent: float = 0.0) -> None:
        pdf.set_font(_FAMILY, "B" if bold else "", size)
        pdf.set_x(pdf.l_margin + indent)
        pdf.multi_cell(
            pdf.epw - indent,
            size * _PT_TO_MM * _LINE,
            text,
            align="L",
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    write(title, _SIZES["title"], bold=True)
    pdf.set_draw_color(120, 160, 180)
    pdf.line(pdf.l_margin, pdf.get_y() + 1.5, pdf.l_margin + pdf.epw, pdf.get_y() + 1.5)
    pdf.ln(6)

    def list_item(marker: str, text: str) -> None:
        # Marker in the gutter, text indented so wrapped lines line up under the first.
        size = _SIZES["body"]
        if pdf.will_page_break(size * _PT_TO_MM * _LINE):
            pdf.add_page()
        top = pdf.get_y()
        pdf.set_font(_FAMILY, "", size)
        pdf.set_xy(pdf.l_margin + 1.0, top)
        pdf.cell(_LIST_INDENT_MM - 1.0, size * _PT_TO_MM * _LINE, marker)
        pdf.set_xy(pdf.l_margin, top)
        write(text, size, indent=_LIST_INDENT_MM)
        pdf.ln(1)

    previous: BlockKind | None = None
    for block in blocks:
        if block.kind.startswith("heading"):
            pdf.ln(4 if previous is not None else 0)
            write(block.text, _SIZES[block.kind], bold=True)
            pdf.ln(1.5)
        elif block.kind == "paragraph":
            write(block.text, _SIZES["body"])
            pdf.ln(2.5)
        else:
            list_item("•" if block.kind == "bullet" else f"{block.number}.", block.text)
        previous = block.kind
    return bytes(pdf.output())


@dataclass(frozen=True)
class FpdfWriter:
    """PdfWriter backed by fpdf2; saves into the project's Outputs folder."""

    config: PdfConfig
    root: Path

    def save(self, title: str, content: str, file_name: str | None) -> SavedPdf:
        now = datetime.now().astimezone()
        if len(content) > self.config.max_content_chars:
            raise PdfError("the document is too long for one PDF")
        blocks = parse_blocks(content, title)
        if not blocks:
            raise PdfError("the document has no content")
        data = render_pdf(title, blocks, self.config, now)
        return self._store(data, file_name or title, now)

    def save_designed(self, design: SafeDesign, file_name: str | None) -> SavedPdf:
        """Render a sanitised design (see designed_renderer.py) and save it the same way."""
        from jarvis.documents.designed_renderer import render_designed

        now = datetime.now().astimezone()
        try:
            data = render_designed(design, self.config, now)
        except PdfError:
            raise
        except Exception as exc:  # a layout the renderer can't draw: caller falls back
            raise PdfError(
                f"the designed layout could not be drawn ({type(exc).__name__})"
            ) from exc
        return self._store(data, file_name or design.title, now)

    def _store(self, data: bytes, name: str, now: datetime) -> SavedPdf:
        folder, created, fallback = output_folder(self.root, self.config)
        path, renamed = write_exclusive(folder, safe_stem(name, now), data)
        return SavedPdf(
            path=path,
            folder_name=folder.name,
            folder_created=created,
            fallback_folder=fallback,
            renamed=renamed,
        )
