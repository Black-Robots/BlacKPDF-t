"""Unir varios PDF en uno."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf, source_stem
from .errors import EmptyInputError
from .progress import ProgressCallback, report


def merge_documents(
    sources: Sequence[PdfSource],
    *,
    add_bookmarks: bool = True,
    passwords: Mapping[int, str] | None = None,
    progress: ProgressCallback | None = None,
) -> pymupdf.Document:
    """Concatena los documentos en el orden dado y devuelve el resultado.

    Con `add_bookmarks` cada archivo aparece como marcador de primer nivel y
    sus marcadores originales quedan anidados debajo.
    """
    if not sources:
        raise EmptyInputError("no hay archivos que unir")
    passwords = passwords or {}
    total = len(sources)
    result = pymupdf.open()
    toc: list[list] = []
    for position, source in enumerate(sources):
        report(progress, position, total + 1)
        doc = open_pdf(source, passwords.get(position))
        try:
            start = result.page_count
            result.insert_pdf(doc)
            if add_bookmarks:
                toc.append([1, source_stem(source, f"Documento {position + 1}"), start + 1])
                for level, title, page, *_ in doc.get_toc(simple=True):
                    if page >= 1:
                        toc.append([level + 1, title, page + start])
        finally:
            doc.close()
    if add_bookmarks and toc:
        result.set_toc(toc)
    report(progress, total, total + 1)
    return result


def merge_pdfs(
    sources: Sequence[PdfSource],
    output: str | os.PathLike,
    *,
    add_bookmarks: bool = True,
    passwords: Mapping[int, str] | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    doc = merge_documents(sources, add_bookmarks=add_bookmarks, passwords=passwords, progress=progress)
    try:
        path = save_pdf(doc, output, garbage=4)
        report(progress, 1, 1)
        return path
    finally:
        doc.close()
