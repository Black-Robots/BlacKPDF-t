"""Limpiar datos ocultos antes de compartir un PDF.

Quita lo que no se ve pero viaja dentro del archivo: metadatos (autor, programa,
fechas), JavaScript, archivos adjuntos, miniaturas... Las opciones que cambian lo que
se ve (comentarios, enlaces, formularios, texto invisible) van aparte y desmarcadas.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf
from .progress import ProgressCallback, report

OPTIONS = ("metadata", "javascript", "attachments", "thumbnails", "comments", "links", "forms", "hidden_text")
DEFAULTS = {"metadata": True, "javascript": True, "attachments": True, "thumbnails": True,
            "comments": False, "links": False, "forms": False, "hidden_text": False}

_COMMENT_TYPES = {
    pymupdf.PDF_ANNOT_TEXT, pymupdf.PDF_ANNOT_FREE_TEXT, pymupdf.PDF_ANNOT_LINE, pymupdf.PDF_ANNOT_SQUARE,
    pymupdf.PDF_ANNOT_CIRCLE, pymupdf.PDF_ANNOT_POLYGON, pymupdf.PDF_ANNOT_POLY_LINE, pymupdf.PDF_ANNOT_HIGHLIGHT,
    pymupdf.PDF_ANNOT_UNDERLINE, pymupdf.PDF_ANNOT_SQUIGGLY, pymupdf.PDF_ANNOT_STRIKE_OUT,
    pymupdf.PDF_ANNOT_STAMP, pymupdf.PDF_ANNOT_CARET, pymupdf.PDF_ANNOT_INK, pymupdf.PDF_ANNOT_POPUP,
    pymupdf.PDF_ANNOT_SOUND,
}
_JS = re.compile(rb"/(JS|JavaScript)\b")


@dataclass
class Findings:
    """Lo que hay en el documento (antes de limpiar)."""

    metadata: list[str] = field(default_factory=list)
    """Campos de metadatos con contenido (title, author...)."""
    xmp: bool = False
    javascript: int = 0
    attachments: int = 0
    thumbnails: int = 0
    comments: int = 0
    links: int = 0
    form_fields: int = 0


def inspect(doc: pymupdf.Document) -> Findings:
    found = Findings()
    meta = doc.metadata or {}
    found.metadata = [k for k in ("title", "author", "subject", "keywords", "creator", "producer",
                                  "creationDate", "modDate") if (meta.get(k) or "").strip()]
    found.xmp = bool(doc.get_xml_metadata().strip())
    for xref in range(1, doc.xref_length()):
        try:
            text = doc.xref_object(xref, compressed=True).encode("latin-1", "replace")
        except Exception:
            continue
        if _JS.search(text):
            found.javascript += 1
    found.attachments = doc.embfile_count()
    for page in doc:
        if doc.xref_get_key(page.xref, "Thumb")[0] != "null":
            found.thumbnails += 1
        for annot in page.annots():
            kind = annot.type[0]
            if kind == pymupdf.PDF_ANNOT_FILE_ATTACHMENT:
                found.attachments += 1
            elif kind in _COMMENT_TYPES and kind != pymupdf.PDF_ANNOT_POPUP:
                found.comments += 1
        found.links += len(page.get_links())
        found.form_fields += sum(1 for _ in page.widgets())
    return found


def _delete_keys(doc: pymupdf.Document, xref: int, *paths: str) -> None:
    """Borra claves de un diccionario («Names/JavaScript»); si un subdiccionario queda vacío, también."""
    from pymupdf import mupdf

    root = mupdf.pdf_load_object(pymupdf._as_pdf_document(doc), xref)
    for path in paths:
        parts = path.split("/")
        parent, chain = root, []
        for part in parts[:-1]:
            child = mupdf.pdf_dict_gets(parent, part)
            if not child.m_internal or not mupdf.pdf_is_dict(child):
                parent = None
                break
            chain.append((parent, part))
            parent = child
        if parent is None:
            continue
        mupdf.pdf_dict_dels(parent, parts[-1])
        for holder, key in reversed(chain):
            child = mupdf.pdf_dict_gets(holder, key)
            if mupdf.pdf_dict_len(child) == 0:
                mupdf.pdf_dict_dels(holder, key)


def _finish_scrub(doc: pymupdf.Document, javascript: bool, attachments: bool) -> None:
    """Lo que scrub deja a medias: acciones de JavaScript vacías y el panel de adjuntos al abrir."""
    catalog = doc.pdf_catalog()
    if javascript:
        kind, value = doc.xref_get_key(catalog, "OpenAction")
        target = doc.xref_object(int(value.split()[0]), compressed=True) if kind == "xref" else value
        if "JavaScript" in (target or ""):
            _delete_keys(doc, catalog, "OpenAction")
        _delete_keys(doc, catalog, "AA", "Names/JavaScript")
        for page in doc:
            _delete_keys(doc, page.xref, "AA")
    if attachments:
        if doc.xref_get_key(catalog, "PageMode")[1] == "/UseAttachments":
            doc.xref_set_key(catalog, "PageMode", "/UseNone")
        _delete_keys(doc, catalog, "Names/EmbeddedFiles")


@dataclass
class CleanResult:
    path: Path
    before: Findings
    after: Findings


def clean_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    options: dict | None = None,
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> CleanResult:
    """Guarda en ``output`` una copia sin los datos ocultos elegidos (el original no se toca)."""
    chosen = {**DEFAULTS, **(options or {})}
    doc = open_pdf(source, password)
    try:
        report(progress, 0, 3)
        before = inspect(doc)
        if chosen["comments"]:
            for page in doc:
                for annot in list(page.annots()):
                    if annot.type[0] in _COMMENT_TYPES:
                        page.delete_annot(annot)
        report(progress, 1, 3)
        doc.scrub(
            attached_files=chosen["attachments"], embedded_files=chosen["attachments"], clean_pages=True,
            hidden_text=chosen["hidden_text"], javascript=chosen["javascript"], metadata=chosen["metadata"],
            redactions=False, redact_images=0, remove_links=chosen["links"], reset_fields=chosen["forms"],
            reset_responses=chosen["comments"], thumbnails=chosen["thumbnails"], xml_metadata=chosen["metadata"],
        )
        _finish_scrub(doc, chosen["javascript"], chosen["attachments"])
        report(progress, 2, 3)
        # garbage=4 elimina también los objetos que se han quedado sin usar (adjuntos, scripts...).
        path = save_pdf(doc, output, garbage=4, deflate=True)
        after = inspect(pymupdf.open(path))
        report(progress, 3, 3)
        return CleanResult(Path(path), before, after)
    finally:
        doc.close()
