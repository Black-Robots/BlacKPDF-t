"""Documento abierto en el editor: PDF en memoria, historial y estado."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

import pymupdf
from PySide6.QtCore import QObject, QSizeF, Signal

from ..core import document as core_document
from ..core.history import History


class PdfDocument(QObject):
    """Envoltorio Qt de un `pymupdf.Document` editable.

    Toda modificación pasa por `edit()`, que guarda una instantánea previa
    para deshacer (máximo 30) y emite `changed` con las páginas afectadas
    (None = cambió la estructura: páginas añadidas, borradas o movidas).
    """

    changed = Signal(object)
    aboutToReplace = Signal()
    modifiedChanged = Signal(bool)
    historyChanged = Signal()
    pathChanged = Signal(str)

    def __init__(self, doc: pymupdf.Document, path: str | None = None, password: str | None = None,
                 encrypted: bool = False, parent: QObject | None = None):
        super().__init__(parent)
        self._doc = doc
        self._path = str(path) if path else ""
        self._password = password
        self._encrypted = encrypted
        self._permissions = doc.permissions if encrypted else -1
        self._history = History()
        self._modified = False
        self._sizes: list[QSizeF] = []
        self._untitled_name = ""
        self._source_suffix = ".pdf"
        self._recompute_sizes()

    # ---------- creación ----------
    @classmethod
    def open(cls, path: str, password: str | None = None) -> "PdfDocument":
        data = core_document.read_file(path)
        encrypted = False
        try:
            with pymupdf.open(stream=data, filetype="pdf") as probe:
                encrypted = bool(probe.is_encrypted or probe.needs_pass)
        except Exception:
            encrypted = False
        doc = core_document.open_pdf(data if Path(path).suffix.lower() == ".pdf" else path, password)
        is_pdf = Path(path).suffix.lower() == ".pdf"
        model = cls(doc, path if is_pdf else None, password, encrypted)
        if not is_pdf:
            model._untitled_name = Path(path).stem + ".pdf"
            model._source_suffix = Path(path).suffix.lower()
            model._modified = True
        return model

    @classmethod
    def from_bytes(cls, data: bytes, name: str) -> "PdfDocument":
        model = cls(core_document.open_pdf(data))
        model._untitled_name = name
        model._modified = True
        return model

    # ---------- propiedades ----------
    @property
    def doc(self) -> pymupdf.Document:
        return self._doc

    @property
    def path(self) -> str:
        return self._path

    @property
    def display_name(self) -> str:
        if self._path:
            return Path(self._path).name
        return self._untitled_name or self.tr("Sin título.pdf")

    @property
    def short_name(self) -> str:
        """Nombre sin extensión, para pestañas."""
        return Path(self.display_name).stem

    @property
    def format_suffix(self) -> str:
        """Formato de origen (".pdf", ".png", ".epub"...); tras guardar, siempre ".pdf"."""
        return ".pdf" if self._path else self._source_suffix

    @property
    def suggested_path(self) -> str:
        if self._path:
            return self._path
        return self._untitled_name or "documento.pdf"

    @property
    def page_count(self) -> int:
        return self._doc.page_count

    @property
    def is_modified(self) -> bool:
        return self._modified

    @property
    def is_encrypted(self) -> bool:
        return self._encrypted

    @property
    def history(self) -> History:
        return self._history

    def page_size(self, index: int) -> QSizeF:
        """Tamaño visible (con rotación) en puntos."""
        return self._sizes[index]

    def page_sizes(self) -> list[QSizeF]:
        return self._sizes

    def snapshot(self) -> bytes:
        """Contenido actual (incluye cambios sin guardar), para herramientas."""
        return self._doc.tobytes()

    # ---------- edición ----------
    def _recompute_sizes(self, pages: Iterable[int] | None = None) -> None:
        if pages is None or len(self._sizes) != self._doc.page_count:
            self._sizes = []
            for page in self._doc:
                rect = page.rect
                self._sizes.append(QSizeF(rect.width, rect.height))
            return
        for index in pages:
            rect = self._doc[index].rect
            self._sizes[index] = QSizeF(rect.width, rect.height)

    def _set_modified(self, value: bool) -> None:
        if value != self._modified:
            self._modified = value
            self.modifiedChanged.emit(value)

    def edit(self, label: str, operation: Callable[[pymupdf.Document], object],
             pages: Iterable[int] | None = None):
        """Aplica `operation(doc)` con posibilidad de deshacer.

        Si la operación falla a medias se restaura el estado anterior.
        """
        before = self._doc.tobytes()
        try:
            result = operation(self._doc)
        except BaseException:
            self._replace(core_document.restore(before))
            self.changed.emit(None)
            raise
        self._history.push(label, before)
        affected = None if pages is None else sorted(set(pages))
        self._recompute_sizes(affected)
        self._set_modified(True)
        self.historyChanged.emit()
        self.changed.emit(affected)
        return result

    def _replace(self, new_doc: pymupdf.Document) -> None:
        self.aboutToReplace.emit()
        old = self._doc
        self._doc = new_doc
        self._recompute_sizes()
        try:
            old.close()
        except Exception:
            pass

    def can_undo(self) -> bool:
        return self._history.can_undo()

    def can_redo(self) -> bool:
        return self._history.can_redo()

    def undo(self) -> None:
        if not self._history.can_undo():
            return
        data = self._history.undo(self._doc.tobytes())
        self._replace(core_document.restore(data))
        self._set_modified(True)
        self.historyChanged.emit()
        self.changed.emit(None)

    def redo(self) -> None:
        if not self._history.can_redo():
            return
        data = self._history.redo(self._doc.tobytes())
        self._replace(core_document.restore(data))
        self._set_modified(True)
        self.historyChanged.emit()
        self.changed.emit(None)

    # ---------- guardado ----------
    def save(self, path: str | None = None) -> str:
        """Guarda en `path` (o en la ruta actual). Conserva el cifrado original."""
        target = path or self._path
        if not target:
            raise ValueError("sin ruta de destino")
        options: dict = {"garbage": 3, "deflate": True}
        if self._encrypted and self._password:
            options.update(
                encryption=pymupdf.PDF_ENCRYPT_AES_256,
                user_pw=self._password,
                owner_pw=self._password,
                permissions=self._permissions if self._permissions != -1 else pymupdf.PDF_PERM_PRINT,
            )
        core_document.save_pdf(self._doc, target, **options)
        if target != self._path:
            self._path = str(target)
            self.pathChanged.emit(self._path)
        self._set_modified(False)
        return self._path
