"""Alternar (intercalar) las páginas de dos PDF.

Caso típico: un escáner sin doble cara produce un PDF con las caras
delanteras y otro con las traseras en orden inverso; con `reverse_b` se
recupera el orden correcto.
"""

from __future__ import annotations

import os
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf
from .progress import ProgressCallback, report


def interleave_order(count_a: int, count_b: int, reverse_b: bool = False) -> list[int]:
    """Orden de páginas sobre la concatenación A+B.

    A1, B1, A2, B2... Si un documento es más largo, el resto va al final.
    """
    a = list(range(count_a))
    b = list(range(count_a, count_a + count_b))
    if reverse_b:
        b.reverse()
    order: list[int] = []
    for i in range(max(count_a, count_b)):
        if i < count_a:
            order.append(a[i])
        if i < count_b:
            order.append(b[i])
    return order


def alternate_documents(
    source_a: PdfSource,
    source_b: PdfSource,
    *,
    reverse_b: bool = False,
    password_a: str | None = None,
    password_b: str | None = None,
) -> pymupdf.Document:
    doc_a = open_pdf(source_a, password_a)
    doc_b = open_pdf(source_b, password_b)
    try:
        result = pymupdf.open()
        result.insert_pdf(doc_a)
        result.insert_pdf(doc_b)
        result.select(interleave_order(doc_a.page_count, doc_b.page_count, reverse_b))
        return result
    finally:
        doc_a.close()
        doc_b.close()


def alternate_pdfs(
    source_a: PdfSource,
    source_b: PdfSource,
    output: str | os.PathLike,
    *,
    reverse_b: bool = False,
    password_a: str | None = None,
    password_b: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    report(progress, 0, 2)
    doc = alternate_documents(
        source_a, source_b, reverse_b=reverse_b, password_a=password_a, password_b=password_b
    )
    try:
        report(progress, 1, 2)
        path = save_pdf(doc, output, garbage=4)
        report(progress, 2, 2)
        return path
    finally:
        doc.close()
