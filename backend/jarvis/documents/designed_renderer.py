"""Draws a sanitised :class:`SafeDesign` as a designed PDF, fully offline.

Everything is vector drawing and text with fpdf2: themed page colours, a cover page or a
title block, a three-level heading hierarchy, callout boxes with small vector icons, striped
tables, bar / line / pie charts, key-figure boxes and dividers. Text uses the same
Devanagari-capable font and HarfBuzz shaping as the plain PDF (Nirmala UI by default).

Only text and shapes are placed: no images, links or remote resources, so rendering makes
no network requests whatever the design contains.
"""

from __future__ import annotations

import logging
from datetime import datetime

from jarvis.config import PdfConfig
from jarvis.documents.design import RGB, THEMES, SafeBlock, SafeDesign, Theme
from jarvis.documents.pdf_writer import resolve_font

_FAMILY = "JarvisSans"
_PT = 0.3528  # mm per point
_LINE = 1.5
_BODY = 10.5
_MARGIN = 20.0

_STYLE_COLORS: dict[str, RGB] = {
    "tip": (46, 160, 90),
    "warning": (230, 140, 0),
    "success": (40, 150, 80),
}
_DEFAULT_ICONS = {"info": "info", "tip": "tip", "warning": "warning", "success": "check"}


def _has_devanagari(text: str) -> bool:
    return any("ऀ" <= ch <= "ॿ" for ch in text)


def render_designed(design: SafeDesign, config: PdfConfig, now: datetime) -> bytes:
    from fpdf import FPDF
    from fpdf.enums import MethodReturnValue, XPos, YPos
    from fpdf.fonts import FontFace

    logging.getLogger("fontTools.subset").setLevel(logging.ERROR)
    theme: Theme = THEMES[design.theme]
    font = resolve_font(config)

    class DesignedPDF(FPDF):
        cover_pages = 0

        def header(self) -> None:
            # Paint the page colour ourselves on every page (fpdf2's page background is lost
            # when a table breaks across pages), then the running header.
            on_cover = self.page_no() <= self.cover_pages
            self.set_fill_color(*(theme.cover_bg if on_cover else theme.page))
            self.rect(0, 0, self.w, self.h, style="F")
            if self.page_no() <= max(1, self.cover_pages):
                return
            self.set_font(_FAMILY, "", 8)
            self.set_text_color(*theme.muted)
            self.set_xy(_MARGIN, 9)
            self.cell(self.epw, 4, design.title[:90], align="L")
            self.set_draw_color(*theme.rule)
            self.set_line_width(0.2)
            self.line(_MARGIN, 14, self.w - _MARGIN, 14)
            self.set_y(_MARGIN)

        def footer(self) -> None:
            if self.page_no() <= self.cover_pages:
                return
            self.set_y(-13)
            self.set_font(_FAMILY, "", 8)
            self.set_text_color(*theme.muted)
            self.cell(0, 4, str(self.page_no() - self.cover_pages), align="R")

    pdf = DesignedPDF(format="A4", unit="mm")
    pdf.set_margins(_MARGIN, _MARGIN, _MARGIN)
    pdf.set_auto_page_break(True, margin=18)
    pdf.add_font(_FAMILY, "", font, collection_font_number=config.font_index_regular)
    pdf.add_font(_FAMILY, "B", font, collection_font_number=config.font_index_bold)
    pdf.set_text_shaping(True)
    pdf.set_title(design.title)
    pdf.set_author("J.A.R.V.I.S.")
    pdf.set_creator("J.A.R.V.I.S.")
    pdf.set_creation_date(now)
    pdf.set_lang("hi" if _has_devanagari(design.title) else "en")

    def text(
        value: str,
        size: float,
        *,
        color: RGB = theme.text,
        bold: bool = False,
        x: float | None = None,
        w: float | None = None,
        align: str = "L",
    ) -> None:
        pdf.set_font(_FAMILY, "B" if bold else "", size)
        pdf.set_text_color(*color)
        left = pdf.l_margin if x is None else x
        pdf.set_x(left)
        pdf.multi_cell(
            w if w is not None else pdf.l_margin + pdf.epw - left,
            size * _PT * _LINE,
            value,
            align=align,
            new_x=XPos.LMARGIN,
            new_y=YPos.NEXT,
        )

    def height_of(value: str, size: float, w: float, *, bold: bool = False) -> float:
        pdf.set_font(_FAMILY, "B" if bold else "", size)
        result = pdf.multi_cell(
            w, size * _PT * _LINE, value, dry_run=True, output=MethodReturnValue.HEIGHT
        )
        return float(result) if isinstance(result, int | float) else 0.0

    def room(height: float) -> None:
        if pdf.will_page_break(height):
            pdf.add_page()

    # -- cover / title ----------------------------------------------------------------

    def cover() -> None:
        pdf.cover_pages = 1
        pdf.add_page()
        draw_cover_art()
        pdf.set_y(92)
        text(design.title, 30, color=theme.cover_text, bold=True)
        pdf.set_fill_color(*theme.accent)
        pdf.rect(pdf.l_margin, pdf.get_y() + 3, 38, 1.6, style="F")
        pdf.ln(9)
        if design.subtitle:
            text(design.subtitle, 14, color=theme.cover_text)
        pdf.set_y(pdf.h - 40)
        text(f"{now:%d %B %Y}", 10, color=theme.cover_text)
        text("Prepared by J.A.R.V.I.S.", 9, color=theme.cover_text)
        pdf.add_page()

    def draw_cover_art() -> None:
        # Concentric arcs and tick marks in the corner: a quiet nod to the HUD.
        cx, cy = pdf.w - 25, pdf.h - 70
        with pdf.local_context(stroke_opacity=0.5):
            pdf.set_draw_color(*theme.accent)
            for i, r in enumerate((18, 30, 44, 60, 78)):
                pdf.set_line_width(0.6 if i % 2 == 0 else 0.25)
                pdf.circle(cx, cy, r, style="D")
        pdf.set_fill_color(*theme.primary)
        pdf.rect(0, 0, pdf.w, 6, style="F")

    def title_block() -> None:
        pdf.add_page()
        pdf.set_fill_color(*theme.primary)
        pdf.rect(0, 0, pdf.w, 4, style="F")
        pdf.set_y(_MARGIN + 2)
        text(design.title, 24, color=theme.primary, bold=True)
        if design.subtitle:
            pdf.ln(1)
            text(design.subtitle, 12, color=theme.muted)
        pdf.ln(1)
        text(f"{now:%d %B %Y}", 8.5, color=theme.muted)
        pdf.set_draw_color(*theme.rule)
        pdf.set_line_width(0.3)
        pdf.line(pdf.l_margin, pdf.get_y() + 2, pdf.l_margin + pdf.epw, pdf.get_y() + 2)
        pdf.ln(7)

    # -- blocks -------------------------------------------------------------------------

    # How much of the next block must fit under a heading, so a heading never ends a page.
    keep_with_next = {"chart": 80.0, "key_figures": 34.0, "table": 30.0, "callout": 26.0}
    upcoming: list[SafeBlock | None] = [None]

    def heading(block: SafeBlock) -> None:
        sizes = {1: 17.0, 2: 13.5, 3: 11.5}
        size = sizes[block.level]
        following = upcoming[0]
        need = keep_with_next.get(following.kind, 14.0) if following is not None else 0.0
        room(size * _PT * _LINE + 10 + need)
        pdf.ln(5 if block.level == 1 else 3)
        if block.level == 1:
            top = pdf.get_y()
            pdf.set_fill_color(*theme.primary)
            pdf.rect(pdf.l_margin, top + 1.2, 1.8, size * _PT * 1.1, style="F")
            text(block.text, size, color=theme.primary, bold=True, x=pdf.l_margin + 5)
            pdf.set_draw_color(*theme.rule)
            pdf.set_line_width(0.25)
            pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.l_margin + pdf.epw, pdf.get_y() + 1)
            pdf.ln(4)
        else:
            color = theme.primary if block.level == 2 else theme.text
            text(block.text, size, color=color, bold=True)
            pdf.ln(1.5)

    def paragraph(block: SafeBlock) -> None:
        text(block.text, _BODY)
        pdf.ln(2.5)

    def list_items(block: SafeBlock) -> None:
        line = _BODY * _PT * _LINE
        for i, item in enumerate(block.items, start=1):
            room(line + 1)
            top = pdf.get_y()
            if block.kind == "bullets":
                pdf.set_fill_color(*theme.primary)
                pdf.circle(pdf.l_margin + 2, top + line / 2, 0.9, style="F")
            else:
                pdf.set_font(_FAMILY, "B", _BODY)
                pdf.set_text_color(*theme.primary)
                pdf.set_xy(pdf.l_margin, top)
                pdf.cell(6, line, f"{i}.")
            pdf.set_xy(pdf.l_margin, top)
            text(item, _BODY, x=pdf.l_margin + 7)
            pdf.ln(1)
        pdf.ln(1.5)

    def callout(block: SafeBlock) -> None:
        color = _STYLE_COLORS.get(block.style, theme.primary)
        if block.style == "quote":
            color = theme.accent
        icon = block.icon or _DEFAULT_ICONS.get(block.style)
        inner_x = pdf.l_margin + (14 if icon else 7)
        inner_w = pdf.l_margin + pdf.epw - 5 - inner_x
        body_size = _BODY if block.style != "quote" else 12
        h = height_of(block.text, body_size, inner_w)
        if block.title:
            h += height_of(block.title, _BODY, inner_w, bold=True) + 1
        box_h = h + 9
        room(box_h + 3)
        top = pdf.get_y() + 1
        pdf.set_fill_color(*theme.soft)
        pdf.rect(pdf.l_margin, top, pdf.epw, box_h, style="F", round_corners=True, corner_radius=2)
        pdf.set_fill_color(*color)
        pdf.rect(pdf.l_margin, top, 1.6, box_h, style="F")
        if icon:
            draw_icon(icon, pdf.l_margin + 7.5, top + 7, 2.9, color)
        pdf.set_y(top + 4.5)
        if block.title:
            text(block.title, _BODY, color=color, bold=True, x=inner_x, w=inner_w)
            pdf.ln(1)
        body = f"“{block.text}”" if block.style == "quote" else block.text
        text(body, body_size, x=inner_x, w=inner_w)
        pdf.set_y(top + box_h + 4)

    def table(block: SafeBlock) -> None:
        if block.title:
            room(30)
            text(block.title, _BODY, color=theme.primary, bold=True)
            pdf.ln(1)
        pdf.set_font(_FAMILY, "", 9.5)
        pdf.set_text_color(*theme.text)
        pdf.set_draw_color(*theme.rule)
        pdf.set_line_width(0.2)
        pdf.set_fill_color(*theme.page)  # unstriped rows: page colour, not whatever came before
        # Header row on the primary colour: white on light themes, page-dark on dark ones.
        dark_page = sum(theme.page) < 200
        head_color = theme.page if dark_page else (255, 255, 255)
        head = FontFace(emphasis="BOLD", color=head_color, fill_color=theme.primary)
        with pdf.table(
            borders_layout="HORIZONTAL_LINES",
            cell_fill_color=theme.soft,
            cell_fill_mode="ROWS",
            headings_style=head,
            line_height=9.5 * _PT * 1.6,
            text_align="LEFT",
            padding=1.6,
            width=pdf.epw,
        ) as grid:
            for row in (block.columns, *block.rows):
                cells = grid.row()
                for value in row:
                    cells.cell(value)
        pdf.ln(4)

    def chart(block: SafeBlock) -> None:
        height = 62.0
        room(height + 22)
        if block.title:
            text(
                block.title + (f" ({block.unit})" if block.unit else ""),
                _BODY,
                color=theme.primary,
                bold=True,
            )
            pdf.ln(2)
        top = pdf.get_y()
        if block.chart_type == "pie":
            pie(block, top, height)
        else:
            bars_or_line(block, top, height)
        pdf.set_y(top + height + 10)

    def label(value: float) -> str:
        return f"{value:,.0f}" if value == int(value) else f"{value:,.2f}".rstrip("0").rstrip(".")

    def bars_or_line(block: SafeBlock, top: float, height: float) -> None:
        values = block.values
        low = min(0.0, *values)
        high = max(0.0, *values)
        span = (high - low) or 1.0
        left = pdf.l_margin + 4
        width = pdf.epw - 8
        plot_top, plot_h = top + 5, height - 12

        def y_of(v: float) -> float:
            return plot_top + plot_h * (1 - (v - low) / span)

        pdf.set_draw_color(*theme.rule)
        pdf.set_line_width(0.15)
        for i in range(5):
            y = plot_top + plot_h * i / 4
            pdf.line(left, y, left + width, y)
        pdf.set_line_width(0.4)
        pdf.line(left, y_of(0), left + width, y_of(0))
        n = len(values)
        slot = width / n
        points = []
        for i, (name, value) in enumerate(zip(block.labels, values, strict=True)):
            cx = left + slot * (i + 0.5)
            if block.chart_type == "bar":
                bw = slot * 0.58
                y0, y1 = sorted((y_of(0), y_of(value)))
                pdf.set_fill_color(*theme.chart[0] if value != max(values) else theme.chart[1])
                pdf.rect(
                    cx - bw / 2,
                    y0,
                    bw,
                    max(y1 - y0, 0.3),
                    style="F",
                    round_corners=True,
                    corner_radius=0.8,
                )
            points.append((cx, y_of(value)))
            pdf.set_font(_FAMILY, "B", 7.5)
            pdf.set_text_color(*theme.text)
            pdf.set_xy(cx - slot / 2, y_of(value) - 5)
            pdf.cell(slot, 4, label(value), align="C")
            pdf.set_font(_FAMILY, "", 7.5)
            pdf.set_text_color(*theme.muted)
            pdf.set_xy(cx - slot / 2, plot_top + plot_h + 1.5)
            pdf.cell(slot, 4, name[: max(4, int(slot / 1.6))], align="C")
        if block.chart_type == "line":
            pdf.set_draw_color(*theme.chart[0])
            pdf.set_line_width(0.8)
            pdf.polyline(points)
            pdf.set_fill_color(*theme.chart[1])
            for x, y in points:
                pdf.circle(x, y, 1.1, style="F")

    def pie(block: SafeBlock, top: float, height: float) -> None:
        total = sum(block.values) or 1.0
        d = height - 6
        x0, y0 = pdf.l_margin + 6, top + 2
        angle = 90.0
        for i, value in enumerate(block.values):
            sweep = 360.0 * value / total
            if sweep <= 0:
                continue
            pdf.set_fill_color(*theme.chart[i % len(theme.chart)])
            pdf.set_draw_color(*theme.page)
            pdf.set_line_width(0.6)
            pdf.solid_arc(x0, y0, d, angle - sweep, angle, style="DF")
            angle -= sweep
        # Legend
        lx = x0 + d + 14
        ly = top + 4
        for i, (name, value) in enumerate(zip(block.labels, block.values, strict=True)):
            pdf.set_fill_color(*theme.chart[i % len(theme.chart)])
            pdf.rect(lx, ly + i * 7 + 0.8, 3.2, 3.2, style="F")
            pdf.set_font(_FAMILY, "", 9)
            pdf.set_text_color(*theme.text)
            pdf.set_xy(lx + 5, ly + i * 7)
            share = 100 * value / total
            pct = f"{share:.0f}" if share >= 10 or share == 0 else f"{share:.1f}"
            pdf.cell(pdf.epw - (lx + 5 - pdf.l_margin), 5, f"{name}  {label(value)} ({pct}%)")

    def key_figures(block: SafeBlock) -> None:
        n = len(block.figures)
        gap = 4.0
        w = (pdf.epw - gap * (n - 1)) / n
        h = 26.0
        room(h + 6)
        top = pdf.get_y() + 1
        for i, (value, caption) in enumerate(block.figures):
            x = pdf.l_margin + i * (w + gap)
            pdf.set_fill_color(*theme.soft)
            pdf.rect(x, top, w, h, style="F", round_corners=True, corner_radius=2)
            pdf.set_fill_color(*theme.chart[i % len(theme.chart)])
            pdf.rect(x, top, w, 1.2, style="F")
            pdf.set_y(top + 5)
            text(value, 17, color=theme.primary, bold=True, x=x, w=w, align="C")
            text(caption, 8.5, color=theme.muted, x=x + 2, w=w - 4, align="C")
        pdf.set_y(top + h + 5)

    def divider(_block: SafeBlock) -> None:
        room(12)
        y = pdf.get_y() + 4
        mid = pdf.l_margin + pdf.epw / 2
        pdf.set_draw_color(*theme.rule)
        pdf.set_line_width(0.3)
        pdf.line(pdf.l_margin + 20, y, mid - 5, y)
        pdf.line(mid + 5, y, pdf.l_margin + pdf.epw - 20, y)
        pdf.set_fill_color(*theme.accent)
        pdf.polygon([(mid, y - 2), (mid + 2, y), (mid, y + 2), (mid - 2, y)], style="F")
        pdf.set_y(y + 6)

    def draw_icon(name: str, cx: float, cy: float, r: float, color: RGB) -> None:
        bg = theme.soft
        pdf.set_fill_color(*color)
        pdf.set_draw_color(*color)
        if name == "warning":
            pdf.polygon(
                [(cx, cy - r), (cx + r * 1.1, cy + r * 0.8), (cx - r * 1.1, cy + r * 0.8)],
                style="F",
            )
            pdf.set_fill_color(*bg)
            pdf.rect(cx - 0.3, cy - r * 0.45, 0.6, r * 0.7, style="F")
            pdf.circle(cx, cy + r * 0.5, 0.35, style="F")
        elif name == "star":
            pdf.star(cx, cy, r * 0.45, r, 5, rotate_degrees=-90, style="F")
        elif name == "flag":
            pdf.set_line_width(0.5)
            pdf.line(cx - r * 0.7, cy - r, cx - r * 0.7, cy + r)
            pdf.polygon(
                [(cx - r * 0.7, cy - r), (cx + r, cy - r * 0.55), (cx - r * 0.7, cy - r * 0.1)],
                style="F",
            )
        elif name == "bolt":
            pdf.polygon(
                [
                    (cx + r * 0.2, cy - r),
                    (cx - r * 0.6, cy + r * 0.1),
                    (cx - r * 0.05, cy + r * 0.1),
                    (cx - r * 0.25, cy + r),
                    (cx + r * 0.6, cy - r * 0.15),
                    (cx + r * 0.05, cy - r * 0.15),
                ],
                style="F",
            )
        elif name == "clock":
            pdf.set_line_width(0.45)
            pdf.circle(cx, cy, r, style="D")
            pdf.line(cx, cy, cx, cy - r * 0.6)
            pdf.line(cx, cy, cx + r * 0.45, cy + r * 0.2)
        else:  # info, tip, check: a filled disc with a mark in the box colour
            pdf.circle(cx, cy, r, style="F")
            pdf.set_fill_color(*bg)
            pdf.set_draw_color(*bg)
            if name == "check":
                pdf.set_line_width(0.6)
                pdf.polyline(
                    [
                        (cx - r * 0.45, cy),
                        (cx - r * 0.1, cy + r * 0.38),
                        (cx + r * 0.5, cy - r * 0.35),
                    ]
                )
            elif name == "tip":
                pdf.circle(cx, cy - r * 0.15, r * 0.38, style="F")
                pdf.rect(cx - r * 0.2, cy + r * 0.25, r * 0.4, r * 0.3, style="F")
            else:
                pdf.rect(cx - 0.3, cy - r * 0.15, 0.6, r * 0.7, style="F")
                pdf.circle(cx, cy - r * 0.5, 0.38, style="F")

    draw = {
        "heading": heading,
        "paragraph": paragraph,
        "bullets": list_items,
        "numbered": list_items,
        "callout": callout,
        "table": table,
        "chart": chart,
        "key_figures": key_figures,
        "divider": divider,
    }

    if design.cover:
        cover()
    else:
        title_block()
    for index, block in enumerate(design.blocks):
        nxt = design.blocks[index + 1] if index + 1 < len(design.blocks) else None
        upcoming[0] = nxt
        draw[block.kind](block)
    return bytes(pdf.output())
