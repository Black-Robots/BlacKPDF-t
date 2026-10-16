"""Conversión entre PDF e imágenes."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, read_file, save_pdf, source_stem
from .errors import EmptyInputError, InvalidOptionError, NotPdfError
from .files import safe_filename, unique_path
from .geometry import PAPER_SIZES
from .progress import ProgressCallback, report
from .ranges import parse_page_selection

IMAGE_EXTENSIONS = (
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff",
    ".pnm", ".pgm", ".ppm", ".pbm", ".jxr", ".psd", ".webp",
)
OUTPUT_FORMATS = ("png", "jpg")


def pdf_to_images(
    source: PdfSource,
    out_dir: str | os.PathLike,
    *,
    fmt: str = "png",
    dpi: int = 150,
    pages: str = "",
    jpg_quality: int = 90,
    base_name: str | None = None,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[Path]:
    """Exporta cada página como imagen: `nombre_p001.png`, `nombre_p002.png`..."""
    fmt = fmt.lower().lstrip(".")
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in OUTPUT_FORMATS:
        raise InvalidOptionError(f"formato {fmt}", option="format")
    if not 36 <= dpi <= 1200:
        raise InvalidOptionError(f"resolución {dpi}", option="dpi")
    doc = open_pdf(source, password)
    try:
        indices = parse_page_selection(pages, doc.page_count, allow_empty_all=True)
        folder = Path(out_dir)
        folder.mkdir(parents=True, exist_ok=True)
        base = safe_filename(base_name or source_stem(source))
        digits = max(3, len(str(doc.page_count)))
        outputs: list[Path] = []
        for position, index in enumerate(indices):
            report(progress, position, len(indices))
            pix = doc[index].get_pixmap(dpi=dpi, alpha=False, annots=True)
            target = unique_path(folder / f"{base}_p{index + 1:0{digits}d}.{fmt}")
            if fmt == "jpg":
                pix.save(str(target), output="jpg", jpg_quality=int(jpg_quality))
            else:
                pix.save(str(target), output="png")
            outputs.append(target)
        report(progress, len(indices), len(indices))
        return outputs
    finally:
        doc.close()


def _fit_rect(box: pymupdf.Rect, width: float, height: float) -> pymupdf.Rect:
    """Rectángulo de `width`x`height` escalado para caber centrado en `box`."""
    scale = min(box.width / width, box.height / height)
    w, h = width * scale, height * scale
    x0 = box.x0 + (box.width - w) / 2
    y0 = box.y0 + (box.height - h) / 2
    return pymupdf.Rect(x0, y0, x0 + w, y0 + h)


def images_to_pdf(
    images: Sequence[str | os.PathLike],
    output: str | os.PathLike,
    *,
    page_size: str = "original",
    orientation: str = "auto",
    margin: float = 0.0,
    progress: ProgressCallback | None = None,
) -> Path:
    """Crea un PDF con una página por imagen (o por fotograma en TIFF/GIF).

    page_size: "original" (la página toma el tamaño de la imagen) o una clave
    de `PAPER_SIZES`. orientation: "auto", "portrait" o "landscape".
    margin: margen en puntos alrededor de la imagen.
    """
    if not images:
        raise EmptyInputError("no hay imágenes")
    if page_size != "original" and page_size not in PAPER_SIZES:
        raise InvalidOptionError(f"tamaño {page_size}", option="page_size")
    if orientation not in ("auto", "portrait", "landscape"):
        raise InvalidOptionError(f"orientación {orientation}", option="orientation")
    if margin < 0:
        raise InvalidOptionError("margen negativo", option="margin")

    out = pymupdf.open()
    try:
        for position, image_path in enumerate(images):
            report(progress, position, len(images) + 1)
            data = read_file(image_path)
            suffix = Path(image_path).suffix.lower().lstrip(".") or "png"
            try:
                img_doc = pymupdf.open(stream=data, filetype=suffix)
            except Exception as exc:
                raise NotPdfError(str(exc), path=str(image_path)) from exc
            frames_pdf = None
            try:
                frames = img_doc.page_count
                if frames > 1:
                    # TIFF/GIF con varios fotogramas: una página por fotograma.
                    frames_pdf = pymupdf.open("pdf", img_doc.convert_to_pdf())
                for frame in range(frames):
                    img_rect = img_doc[frame].rect
                    if img_rect.is_empty:
                        raise NotPdfError("imagen vacía", path=str(image_path))
                    page = _new_page_for(out, img_rect, page_size, orientation, margin)
                    box = page.rect + (margin, margin, -margin, -margin)
                    if box.is_empty:
                        box = page.rect
                    target = _fit_rect(box, img_rect.width, img_rect.height)
                    if frames_pdf is None:
                        page.insert_image(target, stream=data)
                    else:
                        page.show_pdf_page(target, frames_pdf, frame)
            finally:
                if frames_pdf is not None:
                    frames_pdf.close()
                img_doc.close()
        report(progress, len(images), len(images) + 1)
        path = save_pdf(out, output, garbage=3)
        report(progress, len(images) + 1, len(images) + 1)
        return path
    finally:
        out.close()


def _new_page_for(
    doc: pymupdf.Document,
    img_rect: pymupdf.Rect,
    page_size: str,
    orientation: str,
    margin: float,
) -> pymupdf.Page:
    if page_size == "original":
        return doc.new_page(width=img_rect.width + 2 * margin, height=img_rect.height + 2 * margin)
    width, height = PAPER_SIZES[page_size]
    landscape = orientation == "landscape" or (
        orientation == "auto" and img_rect.width > img_rect.height
    )
    if landscape:
        width, height = max(width, height), min(width, height)
    else:
        width, height = min(width, height), max(width, height)
    return doc.new_page(width=width, height=height)
