"""Recortar márgenes y redimensionar páginas.

Convenio de coordenadas de PyMuPDF que se respeta aquí:
- `page.rect` es la página tal como se ve (con rotación y CropBox).
- Las funciones de dibujo y la CropBox usan el espacio sin rotar; se pasa
  de uno a otro con `page.derotation_matrix` / `page.rotation_matrix`.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf
from .errors import InvalidOptionError
from .progress import ProgressCallback, report
from .ranges import parse_page_selection

MM = 72 / 25.4

PAPER_SIZES: dict[str, tuple[float, float]] = {
    "A3": (841.89, 1190.55),
    "A4": (595.28, 841.89),
    "A5": (419.53, 595.28),
    "letter": (612.0, 792.0),
    "legal": (612.0, 1008.0),
}


@dataclass(frozen=True)
class Margins:
    """Márgenes en puntos, referidos a la página tal como se ve."""

    top: float = 0.0
    right: float = 0.0
    bottom: float = 0.0
    left: float = 0.0

    @classmethod
    def uniform(cls, value: float) -> "Margins":
        return cls(value, value, value, value)

    def is_zero(self) -> bool:
        return not any((self.top, self.right, self.bottom, self.left))


def visual_to_page_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    return pymupdf.Rect(rect) * page.derotation_matrix


def _apply_visual_crop(page: pymupdf.Page, visual: pymupdf.Rect) -> None:
    visual = pymupdf.Rect(visual) & page.rect
    if visual.is_empty or visual.width < 1 or visual.height < 1:
        raise InvalidOptionError("el recorte deja la página vacía", option="margins")
    unrotated = visual_to_page_rect(page, visual)
    origin = page.cropbox.tl
    page.set_cropbox(unrotated + (origin.x, origin.y, origin.x, origin.y))


def crop_page(page: pymupdf.Page, margins: Margins) -> None:
    rect = page.rect
    visual = pymupdf.Rect(
        rect.x0 + margins.left,
        rect.y0 + margins.top,
        rect.x1 - margins.right,
        rect.y1 - margins.bottom,
    )
    _apply_visual_crop(page, visual)


def content_bbox(page: pymupdf.Page, threshold: int = 245) -> pymupdf.Rect | None:
    """Caja (en coordenadas visibles) que contiene todo lo que no es blanco.

    Se calcula sobre un render a 72 ppp en escala de grises, lo que funciona
    igual para texto, vectores, imágenes y escaneos.
    """
    pix = page.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False, annots=True)
    width, height = pix.width, pix.height
    table = bytes(1 if value < threshold else 0 for value in range(256))
    mask = pix.samples.translate(table)
    stride = pix.stride
    top = bottom = None
    left, right = width, -1
    for y in range(height):
        row = mask[y * stride : y * stride + width]
        first = row.find(1)
        if first < 0:
            continue
        last = row.rfind(1)
        if top is None:
            top = y
        bottom = y
        left = min(left, first)
        right = max(right, last)
    if top is None:
        return None
    scale_x = page.rect.width / width
    scale_y = page.rect.height / height
    return pymupdf.Rect(
        left * scale_x, top * scale_y, (right + 1) * scale_x, (bottom + 1) * scale_y
    )


def auto_crop_page(page: pymupdf.Page, padding: float = 12.0) -> bool:
    """Recorta el margen blanco. Devuelve False si la página está vacía."""
    box = content_bbox(page)
    if box is None:
        return False
    padded = pymupdf.Rect(box.x0 - padding, box.y0 - padding, box.x1 + padding, box.y1 + padding)
    _apply_visual_crop(page, padded & page.rect)
    return True


def crop_pages(
    doc: pymupdf.Document,
    indices: Iterable[int],
    margins: Margins | None = None,
    *,
    auto: bool = False,
    padding: float = 12.0,
    progress: ProgressCallback | None = None,
) -> None:
    targets = list(indices)
    for position, index in enumerate(targets):
        report(progress, position, len(targets))
        page = doc[index]
        if auto:
            auto_crop_page(page, padding)
        elif margins is not None and not margins.is_zero():
            crop_page(page, margins)


def crop_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    margins: Margins | None = None,
    *,
    auto: bool = False,
    padding: float = 12.0,
    pages: str = "",
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    if not auto and (margins is None or margins.is_zero()):
        raise InvalidOptionError("sin márgenes que recortar", option="margins")
    doc = open_pdf(source, password)
    try:
        indices = parse_page_selection(pages, doc.page_count, allow_empty_all=True)
        crop_pages(doc, indices, margins, auto=auto, padding=padding, progress=progress)
        path = save_pdf(doc, output)
        report(progress, 1, 1)
        return path
    finally:
        doc.close()


def target_size(paper: str, orientation: str, width: float, height: float) -> tuple[float, float]:
    if paper not in PAPER_SIZES:
        raise InvalidOptionError(f"papel {paper}", option="paper")
    if orientation not in ("auto", "portrait", "landscape"):
        raise InvalidOptionError(f"orientación {orientation}", option="orientation")
    short, long = sorted(PAPER_SIZES[paper])
    landscape = orientation == "landscape" or (orientation == "auto" and width > height)
    return (long, short) if landscape else (short, long)


def resize_document(
    doc: pymupdf.Document,
    paper: str,
    orientation: str = "auto",
    *,
    progress: ProgressCallback | None = None,
) -> pymupdf.Document:
    """Documento nuevo con cada página escalada y centrada en el papel elegido.

    Las anotaciones se incorporan al contenido para que no se pierdan.
    """
    src = pymupdf.open("pdf", doc.tobytes())
    try:
        src.bake()
        for page in src:
            if page.rotation:
                page.remove_rotation()
        out = pymupdf.open()
        total = src.page_count
        for index, page in enumerate(src):
            report(progress, index, total)
            width, height = target_size(paper, orientation, page.rect.width, page.rect.height)
            new_page = out.new_page(width=width, height=height)
            new_page.show_pdf_page(new_page.rect, src, index, keep_proportion=True)
        toc = doc.get_toc(simple=True)
        if toc:
            out.set_toc(toc)
        return out
    finally:
        src.close()


def resize_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    paper: str = "A4",
    orientation: str = "auto",
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    doc = open_pdf(source, password)
    try:
        out = resize_document(doc, paper, orientation, progress=progress)
        try:
            path = save_pdf(out, output, garbage=4)
        finally:
            out.close()
        report(progress, 1, 1)
        return path
    finally:
        doc.close()
