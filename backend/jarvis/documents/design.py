"""The document design spec: what the Haiku "designer" returns, and how it is made safe.

Approach (a): the designer returns a *structured spec* (theme, cover, sections, callouts,
tables, chart data) and a deterministic renderer on this machine draws it. No HTML, CSS,
SVG or code ever comes back from the model, so there is nothing to execute, nothing that
can reach the network, and nothing to sanitise except plain strings and numbers.

The spec is untrusted. :func:`sanitize` turns a parsed :class:`DocumentDesign` into a
:class:`SafeDesign`: themes, icons, callout styles and chart types must be from the fixed
lists below; text is stripped of control characters and clipped; tables, lists and charts
are bounded; chart numbers must be finite and actually appear in the source text (so the
designer can lay data out but never invent it); and the designed text must still carry
most of the original content (so it cannot quietly drop or rewrite the document).
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

ThemeName = Literal["classic", "dark_blue", "formal_report", "minimal", "warm", "jarvis"]
IconName = Literal["info", "tip", "warning", "check", "star", "clock", "flag", "bolt"]
IconChoice = Literal["none", "info", "tip", "warning", "check", "star", "clock", "flag", "bolt"]
CalloutStyle = Literal["info", "tip", "warning", "success", "quote"]
ChartType = Literal["bar", "line", "pie"]
BlockKind = Literal[
    "heading",
    "paragraph",
    "bullets",
    "numbered",
    "callout",
    "table",
    "chart",
    "key_figures",
    "divider",
]

RGB = tuple[int, int, int]


@dataclass(frozen=True)
class Theme:
    """Colours for one look. All RGB 0..255; nothing here comes from the model."""

    label: str
    page: RGB  # page background
    text: RGB
    muted: RGB
    primary: RGB  # headings, accents
    accent: RGB  # secondary accent (charts, dividers)
    rule: RGB  # lines and table borders
    soft: RGB  # light fill (table stripes, callout backgrounds)
    cover_bg: RGB
    cover_text: RGB
    chart: tuple[RGB, ...]


THEMES: dict[str, Theme] = {
    "classic": Theme(
        label="Classic",
        page=(255, 255, 255),
        text=(33, 37, 41),
        muted=(108, 117, 125),
        primary=(25, 72, 140),
        accent=(220, 120, 30),
        rule=(206, 212, 218),
        soft=(240, 244, 250),
        cover_bg=(25, 72, 140),
        cover_text=(255, 255, 255),
        chart=((25, 72, 140), (220, 120, 30), (46, 139, 87), (142, 68, 173), (52, 152, 219)),
    ),
    "dark_blue": Theme(
        label="Dark blue",
        page=(10, 20, 40),
        text=(225, 234, 245),
        muted=(140, 160, 190),
        primary=(0, 200, 255),
        accent=(255, 154, 46),
        rule=(45, 70, 110),
        soft=(18, 34, 64),
        cover_bg=(4, 12, 28),
        cover_text=(232, 251, 255),
        chart=((0, 200, 255), (255, 154, 46), (120, 220, 160), (190, 140, 255), (255, 99, 132)),
    ),
    "formal_report": Theme(
        label="Formal report",
        page=(255, 255, 255),
        text=(30, 30, 30),
        muted=(110, 110, 110),
        primary=(110, 20, 35),
        accent=(60, 60, 60),
        rule=(190, 190, 190),
        soft=(245, 243, 240),
        cover_bg=(250, 248, 245),
        cover_text=(110, 20, 35),
        chart=((110, 20, 35), (60, 60, 60), (150, 120, 70), (90, 110, 140), (160, 160, 160)),
    ),
    "minimal": Theme(
        label="Minimal",
        page=(255, 255, 255),
        text=(20, 20, 20),
        muted=(120, 120, 120),
        primary=(20, 20, 20),
        accent=(0, 140, 200),
        rule=(225, 225, 225),
        soft=(246, 246, 246),
        cover_bg=(255, 255, 255),
        cover_text=(20, 20, 20),
        chart=((20, 20, 20), (0, 140, 200), (150, 150, 150), (90, 90, 90), (200, 200, 200)),
    ),
    "warm": Theme(
        label="Warm",
        page=(255, 251, 243),
        text=(60, 40, 30),
        muted=(140, 110, 90),
        primary=(196, 80, 20),
        accent=(120, 80, 40),
        rule=(230, 210, 185),
        soft=(252, 238, 220),
        cover_bg=(196, 80, 20),
        cover_text=(255, 246, 232),
        chart=((196, 80, 20), (230, 160, 40), (120, 80, 40), (160, 110, 80), (90, 140, 110)),
    ),
    "jarvis": Theme(
        label="J.A.R.V.I.S.",
        page=(2, 4, 10),
        text=(214, 244, 251),
        muted=(124, 159, 178),
        primary=(0, 229, 255),
        accent=(255, 154, 46),
        rule=(20, 60, 90),
        soft=(8, 22, 40),
        cover_bg=(2, 4, 10),
        cover_text=(232, 251, 255),
        chart=((0, 229, 255), (255, 154, 46), (10, 108, 255), (255, 61, 0), (232, 251, 255)),
    ),
}
DEFAULT_THEME: ThemeName = "classic"

# Hard bounds on the untrusted spec, so a runaway reply can't produce a 900-page PDF.
MAX_BLOCKS = 160
MAX_TEXT_CHARS = 4000
MAX_SHORT_CHARS = 160
MAX_ITEMS = 40
MAX_TABLE_ROWS = 40
MAX_TABLE_COLS = 6
MAX_CHART_POINTS = 12
MAX_FIGURES = 4
# The designed document must keep at least this share of the source text's characters.
MIN_CONTENT_RATIO = 0.6


# -- what the designer returns (parsed with structured outputs) ---------------------------


class KeyFigure(BaseModel):
    value: str = Field(description="A short figure exactly as in the text, e.g. '4.6 billion'.")
    label: str = Field(description="What the figure is, a few words.")


# Kept deliberately flat for structured outputs: no nullable fields (each would be a union),
# just empty defaults. A block fills only the fields its `kind` uses.
class DesignBlock(BaseModel):
    """One element of the document. Fill only the fields that `kind` uses."""

    kind: BlockKind
    level: int = Field(default=2, description="heading: 1, 2 or 3.")
    text: str = Field(default="", description="heading / paragraph / callout text.")
    title: str = Field(default="", description="callout, table or chart title.")
    items: list[str] = Field(default_factory=list, description="bullets / numbered items.")
    style: CalloutStyle = Field(default="info", description="callout style.")
    icon: IconChoice = Field(default="none", description="callout icon, or none.")
    columns: list[str] = Field(default_factory=list, description="table header cells.")
    rows: list[list[str]] = Field(default_factory=list, description="table body rows.")
    chart_type: ChartType = "bar"
    labels: list[str] = Field(default_factory=list, description="chart category labels.")
    values: list[float] = Field(
        default_factory=list, description="chart values, one per label, taken from the text."
    )
    unit: str = Field(default="", description="chart unit, e.g. '%' or 'km'.")
    figures: list[KeyFigure] = Field(default_factory=list, description="key_figures: 2 to 4.")


class DocumentDesign(BaseModel):
    theme: ThemeName
    cover: bool = Field(description="True for a full cover page (long or formal documents).")
    title: str
    subtitle: str = ""
    blocks: list[DesignBlock]


def design_json_schema() -> dict[str, Any]:
    """JSON Schema for structured outputs: every property required, no unions, no $refs.

    Structured outputs compile the schema into a grammar with limits on optional properties
    and unions; letting every field be optional made the schema "too complex". So the
    model always writes every field (empty for the ones a block kind doesn't use), and the
    reply is validated against :class:`DocumentDesign` afterwards.
    """
    raw = DocumentDesign.model_json_schema(mode="serialization")
    defs = raw.pop("$defs", {})

    def tidy(node: object) -> object:
        if isinstance(node, list):
            return [tidy(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return tidy(defs[node["$ref"].rsplit("/", 1)[-1]])
        # Drop annotations ("title", "default"), but never a *property* that is named title.
        out: dict[str, object] = {}
        properties: dict[str, object] = {}
        for key, value in node.items():
            if key in ("default", "title"):
                continue
            if key == "properties" and isinstance(value, dict):
                properties = {name: tidy(sub) for name, sub in value.items()}
                out[key] = properties
            else:
                out[key] = tidy(value)
        if out.get("type") == "object" and properties:
            out["required"] = list(properties)
            out["additionalProperties"] = False
        return out

    schema = tidy(raw)
    if not isinstance(schema, dict):
        raise TypeError("the design schema must be an object")
    return schema


# -- the validated, bounded form the renderer draws ---------------------------------------


@dataclass(frozen=True)
class SafeBlock:
    kind: BlockKind
    level: int = 2
    text: str = ""
    title: str = ""
    items: tuple[str, ...] = ()
    style: CalloutStyle = "info"
    icon: IconName | None = None
    columns: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()
    chart_type: ChartType = "bar"
    labels: tuple[str, ...] = ()
    values: tuple[float, ...] = ()
    unit: str = ""
    figures: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class SafeDesign:
    theme: ThemeName
    cover: bool
    title: str
    subtitle: str
    blocks: tuple[SafeBlock, ...] = field(default_factory=tuple)


class DesignRejectedError(ValueError):
    """The designer's output can't be used safely; the caller falls back to the plain PDF."""


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f​-‏‪-‮⁦-⁩]")
_MD_MARKERS = re.compile(r"(\*\*|__|`|^#{1,6}\s+|^\s*[-*•]\s+|^\s*\d{1,3}[.)]\s+)", re.MULTILINE)


def clean(text: str | None, limit: int) -> str:
    """Plain, single-paragraph text: no control or bidi-override characters, clipped."""
    if not text:
        return ""
    text = _CONTROL.sub("", unicodedata.normalize("NFC", text))
    text = " ".join(text.replace("**", "").replace("`", "").split())
    return text[:limit].rstrip()


def _clean_list(values: list[str] | None, limit_items: int, limit_chars: int) -> tuple[str, ...]:
    cleaned = (clean(v, limit_chars) for v in (values or [])[:limit_items])
    return tuple(v for v in cleaned if v)


def plain_text(markdown: str) -> str:
    """The source content without Markdown markers, for comparisons."""
    return " ".join(_MD_MARKERS.sub(" ", markdown).split())


def _norm_number(text: str) -> str:
    text = text.replace(",", "")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _numbers_in(text: str) -> set[str]:
    """Every number written in ``text``, normalised ("1,000" -> "1000", "4.50" -> "4.5")."""
    return {_norm_number(m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _chart_ok(values: tuple[float, ...], source_numbers: set[str]) -> bool:
    """Every value must be a finite number that is written in the source text."""
    return all(math.isfinite(v) and _norm_number(f"{abs(v):.6f}") in source_numbers for v in values)


def _safe_block(block: DesignBlock, source_numbers: set[str]) -> SafeBlock | None:
    kind = block.kind
    if kind == "heading":
        text = clean(block.text, MAX_SHORT_CHARS)
        level = block.level if block.level in (1, 2, 3) else 2
        return SafeBlock("heading", level=level, text=text) if text else None
    if kind == "paragraph":
        text = clean(block.text, MAX_TEXT_CHARS)
        return SafeBlock("paragraph", text=text) if text else None
    if kind in ("bullets", "numbered"):
        items = _clean_list(block.items, MAX_ITEMS, MAX_TEXT_CHARS)
        return SafeBlock(kind, items=items) if items else None
    if kind == "callout":
        text = clean(block.text, MAX_TEXT_CHARS)
        if not text:
            return None
        return SafeBlock(
            "callout",
            text=text,
            title=clean(block.title, MAX_SHORT_CHARS),
            style=block.style,
            icon=None if block.icon == "none" else block.icon,
        )
    if kind == "table":
        columns = _clean_list(block.columns, MAX_TABLE_COLS, MAX_SHORT_CHARS)
        if not columns:
            return None
        width = len(columns)
        rows = []
        for row in (block.rows or [])[:MAX_TABLE_ROWS]:
            cells = [clean(c, MAX_SHORT_CHARS * 2) for c in row[:width]]
            rows.append(tuple(cells + [""] * (width - len(cells))))
        if not rows:
            return None
        return SafeBlock(
            "table", title=clean(block.title, MAX_SHORT_CHARS), columns=columns, rows=tuple(rows)
        )
    if kind == "chart":
        labels = _clean_list(block.labels, MAX_CHART_POINTS, 40)
        values = tuple(float(v) for v in (block.values or [])[: len(labels)])
        if len(labels) < 2 or len(values) != len(labels):
            return None
        if not _chart_ok(values, source_numbers):
            return None  # invented or garbled data: leave the chart out, keep the text
        chart_type = block.chart_type or "bar"
        if chart_type == "pie" and any(v < 0 for v in values):
            chart_type = "bar"
        return SafeBlock(
            "chart",
            title=clean(block.title, MAX_SHORT_CHARS),
            chart_type=chart_type,
            labels=labels,
            values=values,
            unit=clean(block.unit, 12),
        )
    if kind == "key_figures":
        figures = tuple(
            (clean(f.value, 24), clean(f.label, 60)) for f in (block.figures or [])[:MAX_FIGURES]
        )
        figures = tuple(f for f in figures if f[0] and f[1])
        return SafeBlock("key_figures", figures=figures) if len(figures) >= 2 else None
    if kind == "divider":
        return SafeBlock("divider")
    return None


def _text_chars(design: SafeDesign) -> int:
    total = len(design.title) + len(design.subtitle)
    for b in design.blocks:
        total += len(b.text) + len(b.title) + sum(map(len, b.items))
        total += sum(len(c) for row in b.rows for c in row) + sum(map(len, b.columns))
        total += sum(len(v) + len(lbl) for v, lbl in b.figures)
    return total


def sanitize(design: DocumentDesign, source_markdown: str, title: str) -> SafeDesign:
    """Validate and bound an untrusted design; raise DesignRejectedError if it isn't usable."""
    if design.theme not in THEMES:
        raise DesignRejectedError("unknown theme")
    source = plain_text(source_markdown)
    numbers = _numbers_in(source)
    blocks = [b for b in (_safe_block(raw, numbers) for raw in design.blocks[:MAX_BLOCKS]) if b]
    safe = SafeDesign(
        theme=design.theme,
        cover=bool(design.cover),
        title=clean(design.title, MAX_SHORT_CHARS) or clean(title, MAX_SHORT_CHARS),
        subtitle=clean(design.subtitle, MAX_SHORT_CHARS),
        blocks=tuple(blocks),
    )
    if not any(b.kind not in ("divider", "heading") for b in safe.blocks):
        raise DesignRejectedError("the design has no content")
    if _text_chars(safe) < MIN_CONTENT_RATIO * len(source):
        raise DesignRejectedError("the design left out too much of the document")
    return safe
