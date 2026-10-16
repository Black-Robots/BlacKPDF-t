"""Números de página."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf
from .errors import InvalidOptionError
from .progress import ProgressCallback, report
from .ranges import parse_page_selection
from .text import font_for

POSITIONS = (
    "top-left", "top-center", "top-right",
    "bottom-left", "bottom-center", "bottom-right",
)

FORMAT_PRESETS: dict[str, str] = {
    "number": "{n}",
    "number-total": "{n}/{total}",
    "page": "Página {n}",
    "page-of": "Página {n} de {total}",
}


@dataclass
class PageNumberOptions:
    position: str = "bottom-center"
    template: str = "{n}"
    """Texto con los marcadores {n} (número) y {total} (total)."""
    start: int = 1
    font: str = "helv"
    font_size: float = 11.0
    color: tuple[float, float, float] = (0.25, 0.25, 0.27)
    margin: float = 28.0
    pages: str = ""
    skip_first: bool = False

    def validate(self) -> None:
        if self.position not in POSITIONS:
            raise InvalidOptionError("posición", option="position")
        if self.font_size <= 0 or self.font_size > 200:
            raise InvalidOptionError("tamaño de fuente", option="font_size")
        if self.margin < 0:
            raise InvalidOptionError("margen", option="margin")
        if "{n}" not in self.template:
            raise InvalidOptionError("el formato debe incluir {n}", option="template")
        try:
            self.template.format(n=1, total=1)
        except (KeyError, IndexError, ValueError) as exc:
            raise InvalidOptionError(str(exc), option="template") from exc


def format_number(template: str, number: int, total: int) -> str:
    return template.format(n=number, total=total)


def apply_page_numbers(
    doc: pymupdf.Document,
    options: PageNumberOptions,
    indices: list[int] | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    """Escribe el número en cada página indicada.

    La numeración avanza solo en las páginas numeradas: si se numeran las
    páginas 3-10 empezando en 1, la página 3 lleva el 1. {total} es el número
    de páginas numeradas más el desplazamiento inicial.
    """
    options.validate()
    targets = indices if indices is not None else list(range(doc.page_count))
    if options.skip_first and targets and targets[0] == 0:
        targets = targets[1:]
    total = len(targets) + options.start - 1
    sample = format_number(options.template, total, total)
    font, fontname, fontfile = font_for(options.font, "", sample)
    size = options.font_size
    vertical, horizontal = options.position.split("-")
    for position, index in enumerate(targets):
        report(progress, position, len(targets))
        page = doc[index]
        text = format_number(options.template, options.start + position, total)
        width = font.text_length(text, fontsize=size)
        rect = page.rect
        if horizontal == "left":
            x = rect.x0 + options.margin
        elif horizontal == "right":
            x = rect.x1 - options.margin - width
        else:
            x = rect.x0 + (rect.width - width) / 2
        if vertical == "top":
            y = rect.y0 + options.margin + size * 0.8
        else:
            y = rect.y1 - options.margin
        point = pymupdf.Point(x, y) * page.derotation_matrix
        page.insert_text(
            point,
            text,
            fontsize=size,
            fontname=fontname,
            fontfile=fontfile,
            color=options.color,
            rotate=page.rotation,
        )


def add_page_numbers(
    source: PdfSource,
    output: str | os.PathLike,
    options: PageNumberOptions,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    options.validate()
    doc = open_pdf(source, password)
    try:
        indices = parse_page_selection(options.pages, doc.page_count, allow_empty_all=True)
        apply_page_numbers(doc, options, indices, progress)
        path = save_pdf(doc, output, garbage=3)
        report(progress, 1, 1)
        return path
    finally:
        doc.close()
