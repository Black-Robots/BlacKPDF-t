"""Apertura y guardado de documentos, comunes a todas las herramientas."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Union

import pymupdf

from .errors import (
    BlackPdfError,
    FileOpenError,
    NotPdfError,
    PasswordRequiredError,
    WrongPasswordError,
    WriteError,
)
from .files import atomic_write

PdfSource = Union[str, os.PathLike, bytes, pymupdf.Document]
"""Ruta a un archivo, contenido en memoria o documento ya abierto."""

PDF_EXTENSIONS = (".pdf",)
CONVERTIBLE_EXTENSIONS = (
    ".xps", ".oxps", ".epub", ".cbz", ".fb2", ".mobi",
    ".png", ".jpg", ".jpeg", ".bmp", ".gif", ".tif", ".tiff",
    ".pnm", ".pgm", ".ppm", ".pbm", ".jxr", ".psd", ".webp",
)
OPENABLE_EXTENSIONS = PDF_EXTENSIONS + CONVERTIBLE_EXTENSIONS

pymupdf.TOOLS.mupdf_display_errors(False)
pymupdf.TOOLS.mupdf_display_warnings(False)


def _filetype_for(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    return suffix or "pdf"


def read_file(path: str | os.PathLike) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise FileOpenError(str(exc), path=str(path)) from exc


def open_pdf(source: PdfSource, password: str | None = None) -> pymupdf.Document:
    """Abre `source` y devuelve siempre un documento PDF nuevo en memoria.

    - Las rutas se leen completas a memoria: el archivo no queda bloqueado
      y se puede sobrescribir al guardar (importante en Windows).
    - Un `pymupdf.Document` se copia, así el original nunca se modifica.
    - Los formatos convertibles (imágenes, XPS, EPUB...) se pasan a PDF.
    """
    label = "documento"
    if isinstance(source, pymupdf.Document):
        data = source.tobytes() if source.is_pdf else source.convert_to_pdf()
        filetype = "pdf"
    elif isinstance(source, (bytes, bytearray, memoryview)):
        data = bytes(source)
        filetype = "pdf"
    else:
        path = Path(source)
        label = str(path)
        data = read_file(path)
        filetype = _filetype_for(path)

    try:
        doc = pymupdf.open(stream=data, filetype=filetype)
    except Exception as exc:
        raise NotPdfError(str(exc), path=label) from exc

    if doc.needs_pass:
        if not password:
            doc.close()
            raise PasswordRequiredError(path=label)
        if not doc.authenticate(password):
            doc.close()
            raise WrongPasswordError(path=label)

    if not doc.is_pdf:
        try:
            converted = doc.convert_to_pdf()
        except Exception as exc:
            raise NotPdfError(str(exc), path=label) from exc
        finally:
            doc.close()
        doc = pymupdf.open(stream=converted, filetype="pdf")

    if doc.page_count == 0:
        doc.close()
        raise NotPdfError("sin páginas", path=label)
    return doc


def needs_password(path: str | os.PathLike) -> bool:
    """Indica si el archivo pide contraseña para abrirse."""
    try:
        with pymupdf.open(stream=read_file(path), filetype=_filetype_for(Path(path))) as doc:
            return bool(doc.needs_pass)
    except BlackPdfError:
        raise
    except Exception as exc:
        raise NotPdfError(str(exc), path=str(path)) from exc


def save_pdf(doc: pymupdf.Document, path: str | os.PathLike, **options) -> Path:
    """Guarda `doc` en `path` de forma atómica y con compresión razonable."""
    settings = {"garbage": 3, "deflate": True}
    settings.update(options)
    try:
        return atomic_write(path, lambda tmp: doc.save(tmp, **settings))
    except BlackPdfError:
        raise
    except Exception as exc:
        raise WriteError(str(exc), path=str(path)) from exc


def snapshot(doc: pymupdf.Document) -> bytes:
    """Copia completa del documento para deshacer/rehacer."""
    return doc.tobytes()


def restore(data: bytes) -> pymupdf.Document:
    return pymupdf.open(stream=data, filetype="pdf")


def source_stem(source: PdfSource, fallback: str = "documento") -> str:
    if isinstance(source, (str, os.PathLike)):
        return Path(source).stem or fallback
    if isinstance(source, pymupdf.Document) and source.name:
        return Path(source.name).stem or fallback
    return fallback


def source_size(source: PdfSource) -> int:
    if isinstance(source, (bytes, bytearray, memoryview)):
        return len(source)
    if isinstance(source, pymupdf.Document):
        return len(source.tobytes())
    try:
        return Path(source).stat().st_size
    except OSError:
        return 0


@dataclass(frozen=True)
class DocumentInfo:
    page_count: int
    title: str
    author: str
    subject: str
    creator: str
    producer: str
    created: str
    modified: str
    encrypted: bool
    pdf_version: str
    has_forms: bool
    page_size: tuple[float, float]


def document_info(doc: pymupdf.Document) -> DocumentInfo:
    meta = doc.metadata or {}
    first = doc[0].rect if doc.page_count else pymupdf.Rect()
    return DocumentInfo(
        page_count=doc.page_count,
        title=meta.get("title", "") or "",
        author=meta.get("author", "") or "",
        subject=meta.get("subject", "") or "",
        creator=meta.get("creator", "") or "",
        producer=meta.get("producer", "") or "",
        created=format_pdf_date(meta.get("creationDate", "") or ""),
        modified=format_pdf_date(meta.get("modDate", "") or ""),
        encrypted=bool(meta.get("encryption")),
        pdf_version=(meta.get("format", "") or "").replace("PDF ", ""),
        has_forms=bool(doc.is_form_pdf),
        page_size=(first.width, first.height),
    )


def format_pdf_date(value: str) -> str:
    """`D:20240131120000+01'00'` -> `31/01/2024 12:00`."""
    digits = value[2:] if value.startswith("D:") else value
    if len(digits) < 8 or not digits[:8].isdigit():
        return ""
    year, month, day = digits[0:4], digits[4:6], digits[6:8]
    text = f"{day}/{month}/{year}"
    if len(digits) >= 12 and digits[8:12].isdigit():
        text += f" {digits[8:10]}:{digits[10:12]}"
    return text
