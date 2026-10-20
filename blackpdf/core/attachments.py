"""Archivos adjuntos de un PDF: los del documento y los de anotaciones de página.

Un PDF puede llevar otros archivos dentro (por ejemplo, planos.pdf con el modelo .step):
- adjuntos del documento: la lista de archivos incrustados (EmbeddedFiles);
- adjuntos de página: anotaciones FileAttachment, con un icono en un punto de una página.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from .errors import FileOpenError, WriteError

KIND_DOCUMENT = "document"
KIND_PAGE = "page"

DANGEROUS_EXTENSIONS = frozenset({
    ".exe", ".com", ".scr", ".pif", ".bat", ".cmd", ".msi", ".msp", ".msix", ".appx", ".dll", ".cpl",
    ".ps1", ".psm1", ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".reg", ".jar",
    ".sh", ".bash", ".run", ".bin", ".appimage", ".deb", ".rpm", ".desktop", ".py", ".pyw", ".app",
    ".command", ".url", ".iso", ".img", ".vhd", ".vhdx",
})
"""Programas y scripts: no se abren con doble clic (podrían ejecutar código); solo se guardan."""


@dataclass
class Attachment:
    kind: str
    """KIND_DOCUMENT o KIND_PAGE."""
    name: str
    """Nombre interno (adjunto del documento) o xref de la anotación."""
    filename: str
    size: int
    """Tamaño sin comprimir en bytes (-1 si el PDF no lo indica)."""
    description: str = ""
    page: int | None = None
    xref: int = 0
    rect: tuple[float, float, float, float] | None = None
    """Posición del icono en la página (adjuntos de página)."""

    @property
    def extension(self) -> str:
        return Path(self.filename).suffix.lower()

    @property
    def dangerous(self) -> bool:
        return is_dangerous(self.filename)


def is_dangerous(filename: str) -> bool:
    return Path(filename).suffix.lower() in DANGEROUS_EXTENSIONS


def safe_filename(name: str, fallback: str = "adjunto") -> str:
    """Nombre de archivo sin carpetas ni caracteres no válidos (un PDF podría traer «../../algo»)."""
    name = re.split(r"[\\/]", name or "")[-1]
    name = re.sub(r'[<>:"|?*\x00-\x1f]', "_", name).strip().strip(".")
    if name.upper().split(".")[0] in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                       *(f"LPT{i}" for i in range(1, 10))}:
        name = "_" + name
    return name[:200] or fallback


def _page_attachments(doc: pymupdf.Document) -> list[Attachment]:
    items = []
    for page in doc:
        for annot in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]):
            info = annot.file_info
            description = info.get("description") or annot.info.get("content") or ""
            filename = info.get("filename") or f"adjunto-{annot.xref}"
            rect = annot.rect
            items.append(Attachment(KIND_PAGE, str(annot.xref), filename, int(info.get("size", -1) or -1),
                                    description, page.number, annot.xref, (rect.x0, rect.y0, rect.x1, rect.y1)))
    return items


def list_attachments(doc: pymupdf.Document) -> list[Attachment]:
    """Adjuntos del documento y de sus páginas, en este orden."""
    items = []
    for name in doc.embfile_names():
        info = doc.embfile_info(name)
        filename = info.get("ufilename") or info.get("filename") or name
        items.append(Attachment(KIND_DOCUMENT, name, filename, int(info.get("size", -1) or -1),
                                info.get("description") or ""))
    return items + _page_attachments(doc)


def count_attachments(doc: pymupdf.Document) -> int:
    return doc.embfile_count() + sum(
        1 for page in doc for _ in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]))


def _annotation(doc: pymupdf.Document, attachment: Attachment):
    page = doc[attachment.page]
    for annot in page.annots(types=[pymupdf.PDF_ANNOT_FILE_ATTACHMENT]):
        if annot.xref == attachment.xref:
            return page, annot
    raise FileOpenError("el adjunto ya no está en el documento", path=attachment.filename)


def read_attachment(doc: pymupdf.Document, attachment: Attachment) -> bytes:
    """Contenido del adjunto."""
    try:
        if attachment.kind == KIND_DOCUMENT:
            return doc.embfile_get(attachment.name)
        page, annot = _annotation(doc, attachment)
        return annot.get_file()
    except FileOpenError:
        raise
    except Exception as exc:
        raise FileOpenError(str(exc), path=attachment.filename) from exc


def unique_path(folder: Path, filename: str) -> Path:
    """`folder/filename` sin pisar nada: «plano (2).step» si ya existe."""
    path = folder / filename
    stem, suffix = Path(filename).stem, Path(filename).suffix
    number = 2
    while path.exists():
        path = folder / f"{stem} ({number}){suffix}"
        number += 1
    return path


def save_attachment(doc: pymupdf.Document, attachment: Attachment, path: str) -> str:
    data = read_attachment(doc, attachment)
    try:
        Path(path).write_bytes(data)
    except OSError as exc:
        raise WriteError(str(exc), path=path) from exc
    return str(path)


def save_all(doc: pymupdf.Document, attachments: list[Attachment], folder: str) -> list[str]:
    """Guarda varios adjuntos en una carpeta; nunca sobrescribe ni escribe fuera de ella."""
    target = Path(folder)
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WriteError(str(exc), path=folder) from exc
    return [save_attachment(doc, item, str(unique_path(target, safe_filename(item.filename))))
            for item in attachments]


def add_attachment(doc: pymupdf.Document, path: str, description: str = "") -> str:
    """Adjunta un archivo al documento. Devuelve su nombre interno (único)."""
    source = Path(path)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise FileOpenError(str(exc), path=path) from exc
    existing = set(doc.embfile_names())
    name, number = source.name, 2
    while name in existing:
        name = f"{source.stem} ({number}){source.suffix}"
        number += 1
    doc.embfile_add(name, data, filename=source.name, ufilename=source.name, desc=description or None)
    return name


def remove_attachment(doc: pymupdf.Document, attachment: Attachment) -> None:
    if attachment.kind == KIND_DOCUMENT:
        doc.embfile_del(attachment.name)
        return
    page, annot = _annotation(doc, attachment)
    page.delete_annot(annot)
