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
    IncrementalSaveError,
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


def _same_object(doc: pymupdf.Document, xref: int, text: str, stream: bytes | None) -> bool:
    try:
        if doc.xref_object(xref, compressed=True) != text:
            return False
        if stream is None:
            return not doc.xref_is_stream(xref)
        return doc.xref_is_stream(xref) and doc.xref_stream_raw(xref) == stream
    except Exception:
        return False  # libre en el original o recién reservado


def incremental_update(doc: pymupdf.Document, base: bytes, password: str | None = None) -> bytes:
    """``base`` intacto más una revisión con lo que ``doc`` ha cambiado respecto a él.

    Es como guardan los programas que respetan las firmas digitales: lo firmado queda
    byte a byte igual y los cambios se añaden al final. ``doc`` debe venir de ``base``
    (abierto desde él, aunque haya pasado por deshacer/rehacer): los objetos conservan
    su número y basta con copiar los que difieren. Se hace sobre un documento nuevo
    porque MuPDF no admite dos guardados incrementales con el mismo objeto.
    """
    from pymupdf import mupdf

    target = pymupdf.open(stream=base, filetype="pdf")
    try:
        if target.needs_pass and not target.authenticate(password or ""):
            raise WrongPasswordError()
        if target.is_repaired:
            raise IncrementalSaveError("el original estaba dañado")
        pdf = pymupdf._as_pdf_document(target)
        count = doc.xref_length()
        while target.xref_length() < count:
            target.get_new_xref()
        for xref in range(1, count):
            try:
                text = doc.xref_object(xref, compressed=True)
            except Exception:
                text = ""
            if not text or text == "null":
                continue  # libre: lo que hubiera en el original queda sin usar
            stream = doc.xref_stream_raw(xref) if doc.xref_is_stream(xref) else None
            if _same_object(target, xref, text, stream):
                continue
            target.update_object(xref, text)
            if stream is not None:
                # Tal cual (ya comprimido): el diccionario copiado indica sus filtros.
                mupdf.pdf_update_stream(pdf, mupdf.pdf_load_object(pdf, xref),
                                        mupdf.fz_new_buffer_from_copied_data(stream), 1)
        for key in ("Root", "Info"):
            value = doc.xref_get_key(-1, key)
            if value[0] == "xref" and value != target.xref_get_key(-1, key):
                target.xref_set_key(-1, key, value[1])
        options = mupdf.PdfWriteOptions()
        options.do_incremental = 1
        buffer = mupdf.fz_new_buffer(len(base) + 65536)
        output = mupdf.FzOutput(buffer)
        try:
            mupdf.pdf_write_document(pdf, output, options)
        except Exception as exc:
            raise IncrementalSaveError(str(exc)) from exc
        finally:
            output.fz_close_output()
        data = bytes(mupdf.fz_buffer_extract_copy(buffer))
    finally:
        target.close()
    if not data.startswith(base):
        raise IncrementalSaveError("la revisión no se añadió al final")
    return data


def save_incremental(doc: pymupdf.Document, base: bytes, target: str | os.PathLike,
                     password: str | None = None) -> bytes:
    """Escribe en ``target`` el original con los cambios añadidos al final; devuelve el contenido escrito."""
    data = incremental_update(doc, base, password)
    try:
        atomic_write(target, lambda tmp: Path(tmp).write_bytes(data))
    except OSError as exc:
        raise WriteError(str(exc), path=str(target)) from exc
    return data


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
