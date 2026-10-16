"""Marca de agua de texto o imagen, con opacidad, rotación y posición.

La marca se dibuja primero en un PDF auxiliar de una página (con la
opacidad ya aplicada) y luego se estampa con `show_pdf_page`, que admite
cualquier ángulo y mantiene el resultado vectorial en el caso del texto.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, read_file, save_pdf
from .errors import EmptyInputError, InvalidOptionError, NotPdfError
from .progress import ProgressCallback, report
from .ranges import parse_page_selection
from .text import font_for

POSITIONS = (
    "center", "top-left", "top", "top-right", "left", "right",
    "bottom-left", "bottom", "bottom-right", "tile",
)


@dataclass
class WatermarkOptions:
    text: str = ""
    image_path: str = ""
    font: str = "helv"
    font_file: str = ""
    font_size: float = 48.0
    color: tuple[float, float, float] = (0.898, 0.282, 0.302)
    opacity: float = 0.3
    rotation: float = 45.0
    """Grados en sentido antihorario, como se ve la página."""
    position: str = "center"
    image_scale: float = 0.5
    """Ancho de la imagen respecto al ancho de la página (0-1)."""
    margin: float = 36.0
    behind: bool = False
    """Dibujar debajo del contenido en lugar de encima."""
    pages: str = ""
    tile_gap: float = 1.6

    def validate(self) -> None:
        if not self.text.strip() and not self.image_path:
            raise EmptyInputError("marca de agua vacía")
        if not 0.0 < self.opacity <= 1.0:
            raise InvalidOptionError("opacidad", option="opacity")
        if self.position not in POSITIONS:
            raise InvalidOptionError("posición", option="position")
        if self.font_size <= 0:
            raise InvalidOptionError("tamaño de fuente", option="font_size")
        if not 0.0 < self.image_scale <= 1.0:
            raise InvalidOptionError("escala de imagen", option="image_scale")


def _text_stamp(options: WatermarkOptions) -> pymupdf.Document:
    font, fontname, fontfile = font_for(options.font, options.font_file, options.text)
    lines = [line for line in options.text.splitlines()] or [options.text]
    size = options.font_size
    line_height = size * 1.2
    width = max(font.text_length(line, fontsize=size) for line in lines)
    height = line_height * len(lines)
    pad = size * 0.15
    stamp = pymupdf.open()
    page = stamp.new_page(width=width + 2 * pad, height=height + 2 * pad)
    ascender = font.ascender if font.ascender > 0 else 0.9
    for row, line in enumerate(lines):
        line_width = font.text_length(line, fontsize=size)
        x = pad + (width - line_width) / 2
        y = pad + row * line_height + ascender * size + (line_height - size) / 2
        page.insert_text(
            (x, y),
            line,
            fontsize=size,
            fontname=fontname,
            fontfile=fontfile,
            color=options.color,
            fill_opacity=options.opacity,
            stroke_opacity=options.opacity,
        )
    return stamp


def _image_stamp(options: WatermarkOptions) -> pymupdf.Document:
    data = read_file(options.image_path)
    try:
        pix = pymupdf.Pixmap(data)
    except Exception as exc:
        raise NotPdfError(str(exc), path=options.image_path) from exc
    if pix.colorspace is None or pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    longest = max(pix.width, pix.height)
    if longest > 1600:
        factor = 1600 / longest
        pix = pymupdf.Pixmap(pix, max(1, int(pix.width * factor)), max(1, int(pix.height * factor)), None)
    if not pix.alpha:
        pix = pymupdf.Pixmap(pix, 1)
    if options.opacity < 1.0:
        alpha = pix.samples[pix.n - 1 :: pix.n]
        table = bytes(round(value * options.opacity) for value in range(256))
        pix.set_alpha(alpha.translate(table))
    stamp = pymupdf.open()
    page = stamp.new_page(width=pix.width, height=pix.height)
    page.insert_image(page.rect, pixmap=pix)
    return stamp


def _rotated_size(width: float, height: float, degrees: float) -> tuple[float, float]:
    radians = math.radians(degrees)
    cos, sin = abs(math.cos(radians)), abs(math.sin(radians))
    return width * cos + height * sin, width * sin + height * cos


def _anchor_rect(page_rect: pymupdf.Rect, w: float, h: float, position: str, margin: float) -> pymupdf.Rect:
    left = page_rect.x0 + margin
    right = page_rect.x1 - margin - w
    top = page_rect.y0 + margin
    bottom = page_rect.y1 - margin - h
    cx = page_rect.x0 + (page_rect.width - w) / 2
    cy = page_rect.y0 + (page_rect.height - h) / 2
    x, y = {
        "center": (cx, cy),
        "top-left": (left, top),
        "top": (cx, top),
        "top-right": (right, top),
        "left": (left, cy),
        "right": (right, cy),
        "bottom-left": (left, bottom),
        "bottom": (cx, bottom),
        "bottom-right": (right, bottom),
    }[position]
    return pymupdf.Rect(x, y, x + w, y + h)


def _placements(page: pymupdf.Page, stamp_rect: pymupdf.Rect, options: WatermarkOptions) -> list[pymupdf.Rect]:
    page_rect = page.rect
    if options.image_path and not options.text.strip():
        target_w = page_rect.width * options.image_scale
        scale = target_w / stamp_rect.width
    else:
        scale = 1.0
    w, h = stamp_rect.width * scale, stamp_rect.height * scale
    bw, bh = _rotated_size(w, h, options.rotation)
    fit = min(1.0, page_rect.width / bw, page_rect.height / bh)
    bw, bh = bw * fit, bh * fit
    if options.position != "tile":
        margin = min(options.margin, max(0.0, (page_rect.width - bw) / 2), max(0.0, (page_rect.height - bh) / 2))
        return [_anchor_rect(page_rect, bw, bh, options.position, margin)]
    step_x, step_y = bw * options.tile_gap, bh * options.tile_gap
    rects = []
    columns = max(1, math.ceil(page_rect.width / step_x) + 1)
    rows = max(1, math.ceil(page_rect.height / step_y) + 1)
    offset_x = (page_rect.width - (columns - 1) * step_x - bw) / 2
    offset_y = (page_rect.height - (rows - 1) * step_y - bh) / 2
    for row in range(rows):
        for column in range(columns):
            x = offset_x + column * step_x + (step_x / 2 if row % 2 else 0)
            y = offset_y + row * step_y
            rect = pymupdf.Rect(x, y, x + bw, y + bh)
            if rect.intersects(page_rect):
                rects.append(rect)
    return rects


def build_stamp(options: WatermarkOptions) -> pymupdf.Document:
    options.validate()
    return _text_stamp(options) if options.text.strip() else _image_stamp(options)


def apply_watermark(
    doc: pymupdf.Document,
    options: WatermarkOptions,
    indices: list[int] | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    stamp = build_stamp(options)
    try:
        stamp_rect = stamp[0].rect
        targets = indices if indices is not None else list(range(doc.page_count))
        for position, index in enumerate(targets):
            report(progress, position, len(targets))
            page = doc[index]
            for visual_rect in _placements(page, stamp_rect, options):
                rect = visual_rect * page.derotation_matrix
                page.show_pdf_page(
                    rect,
                    stamp,
                    0,
                    keep_proportion=True,
                    overlay=not options.behind,
                    rotate=(options.rotation + page.rotation) % 360,
                )
    finally:
        stamp.close()


def add_watermark(
    source: PdfSource,
    output: str | os.PathLike,
    options: WatermarkOptions,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    options.validate()
    doc = open_pdf(source, password)
    try:
        indices = parse_page_selection(options.pages, doc.page_count, allow_empty_all=True)
        apply_watermark(doc, options, indices, progress)
        path = save_pdf(doc, output, garbage=3)
        report(progress, 1, 1)
        return path
    finally:
        doc.close()
