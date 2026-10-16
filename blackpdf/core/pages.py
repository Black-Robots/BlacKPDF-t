"""Organización de páginas: rotar, eliminar, duplicar, mover e insertar.

Las funciones `*_pages` modifican el documento recibido; `rotate_pdf` trabaja
de archivo a archivo para la herramienta independiente.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf
from .errors import EmptyInputError, InvalidOptionError, LastPageError
from .progress import ProgressCallback, report
from .ranges import parse_page_selection

A4 = (595.28, 841.89)


def _normalize(doc: pymupdf.Document, indices: Iterable[int]) -> list[int]:
    result = sorted({int(i) for i in indices})
    if not result:
        raise EmptyInputError("sin páginas seleccionadas")
    for index in result:
        if index < 0 or index >= doc.page_count:
            raise InvalidOptionError(f"página {index} fuera de rango", option="pages")
    return result


def rotate_pages(doc: pymupdf.Document, indices: Iterable[int], angle: int) -> None:
    """Gira las páginas `angle` grados en sentido horario (múltiplo de 90)."""
    if angle % 90:
        raise InvalidOptionError("el ángulo debe ser múltiplo de 90", option="angle")
    for index in _normalize(doc, indices):
        page = doc[index]
        page.set_rotation((page.rotation + angle) % 360)


def delete_pages(doc: pymupdf.Document, indices: Iterable[int]) -> None:
    targets = _normalize(doc, indices)
    if len(targets) >= doc.page_count:
        raise LastPageError("un PDF necesita al menos una página")
    doc.delete_pages(targets)


def duplicate_pages(doc: pymupdf.Document, indices: Iterable[int]) -> list[int]:
    """Inserta una copia independiente de cada página justo detrás de ella.

    Devuelve los índices de las copias en el documento resultante.
    """
    targets = _normalize(doc, indices)
    for index in reversed(targets):
        doc.fullcopy_page(index, index + 1 if index + 1 < doc.page_count else -1)
    return [index + offset + 1 for offset, index in enumerate(targets)]


def move_pages(doc: pymupdf.Document, indices: Iterable[int], target: int) -> list[int]:
    """Mueve las páginas seleccionadas para que queden delante de `target`.

    `target` es una posición del orden actual (0..page_count). Las páginas
    movidas conservan su orden relativo. Devuelve sus nuevos índices.
    """
    selected = _normalize(doc, indices)
    count = doc.page_count
    target = max(0, min(int(target), count))
    chosen = set(selected)
    remaining = [i for i in range(count) if i not in chosen]
    insert_at = sum(1 for i in remaining if i < target)
    order = remaining[:insert_at] + selected + remaining[insert_at:]
    if order != list(range(count)):
        doc.select(order)
    return list(range(insert_at, insert_at + len(selected)))


def reorder_pages(doc: pymupdf.Document, order: Sequence[int]) -> None:
    """Aplica un orden completo de páginas (permutación de 0..n-1)."""
    if sorted(order) != list(range(doc.page_count)):
        raise InvalidOptionError("el orden no es una permutación válida", option="order")
    if list(order) != list(range(doc.page_count)):
        doc.select(list(order))


def neighbour_size(doc: pymupdf.Document, index: int) -> tuple[float, float]:
    """Tamaño visible de la página vecina, o A4 si el documento está vacío."""
    if doc.page_count == 0:
        return A4
    ref = doc[min(max(index - 1, 0), doc.page_count - 1)]
    return ref.rect.width, ref.rect.height


def insert_blank_page(
    doc: pymupdf.Document,
    index: int,
    width: float | None = None,
    height: float | None = None,
) -> int:
    """Inserta una página en blanco en `index` y devuelve su posición."""
    index = max(0, min(int(index), doc.page_count))
    if width is None or height is None:
        width, height = neighbour_size(doc, index)
    if width <= 0 or height <= 0:
        raise InvalidOptionError("tamaño de página no válido", option="size")
    doc.new_page(pno=index if index < doc.page_count else -1, width=width, height=height)
    return index


def insert_pdf_pages(
    doc: pymupdf.Document,
    source: PdfSource,
    index: int,
    pages: Sequence[int] | None = None,
    password: str | None = None,
) -> list[int]:
    """Inserta páginas de otro PDF en `index`. Devuelve los nuevos índices."""
    src = open_pdf(source, password)
    try:
        if pages is not None:
            selection = list(pages)
            if not selection:
                raise EmptyInputError("sin páginas que insertar")
            src.select(selection)
        index = max(0, min(int(index), doc.page_count))
        count = src.page_count
        doc.insert_pdf(src, start_at=index if index < doc.page_count else -1)
        return list(range(index, index + count))
    finally:
        src.close()


def extract_document(doc: pymupdf.Document, indices: Sequence[int]) -> pymupdf.Document:
    """Documento nuevo con las páginas indicadas, en ese orden."""
    if not indices:
        raise EmptyInputError("sin páginas seleccionadas")
    out = pymupdf.open()
    for index in indices:
        out.insert_pdf(doc, from_page=index, to_page=index)
    return out


def rotate_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    angle: int,
    pages: str = "",
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    """Herramienta Rotar: gira las páginas indicadas (vacío = todas)."""
    doc = open_pdf(source, password)
    try:
        report(progress, 0, 2)
        indices = parse_page_selection(pages, doc.page_count, allow_empty_all=True)
        rotate_pages(doc, indices, angle)
        report(progress, 1, 2)
        result = save_pdf(doc, output)
        report(progress, 2, 2)
        return result
    finally:
        doc.close()
