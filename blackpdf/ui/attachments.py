"""Panel de archivos adjuntos: ver, abrir, guardar, adjuntar y quitar."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QRectF, QStandardPaths, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QMessageBox,
    QScrollArea,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import attachments as core
from ..core.errors import BlackPdfError
from ..core.model3d import MODEL_EXTENSIONS
from ..core.model3d.insert import INSERTABLE_EXTENSIONS
from . import formatting, icons, platform
from .comments import ColorDot
from .document import PdfDocument
from .errors import show_error
from .settings import settings
from .widgets import Button, Toast, dropped_paths, label

OPEN_IN_BLACKPDF = (".pdf", ".xps", ".oxps", ".epub") + MODEL_EXTENSIONS
"""Se abren en una pestaña de BlackPDF (los modelos 3D, en su visor)."""

TYPE_ICONS = {
    ".pdf": "file-text", ".xps": "file-text", ".epub": "book-open", ".txt": "file-text", ".md": "file-text",
    ".step": "box", ".stp": "box", ".iges": "box", ".igs": "box", ".stl": "box", ".obj": "box", ".prc": "box",
    ".u3d": "box", ".3mf": "box", ".glb": "box", ".gltf": "box", ".dwg": "shapes", ".dxf": "shapes",
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image", ".svg": "image", ".tif": "image",
    ".tiff": "image", ".webp": "image", ".zip": "file-archive", ".7z": "file-archive", ".rar": "file-archive",
    ".gz": "file-archive", ".xlsx": "grid-3x3", ".xls": "grid-3x3", ".csv": "grid-3x3", ".ods": "grid-3x3",
}


def _temp_folder() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
    folder = Path(base) / "BlackPDF-adjuntos" / uuid.uuid4().hex[:10]
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class AttachmentCard(QFrame):
    openRequested = Signal(object)
    insert3dRequested = Signal(object)
    saveRequested = Signal(object)
    removeRequested = Signal(object)
    locateRequested = Signal(object)

    def __init__(self, item: core.Attachment, parent=None):
        super().__init__(parent)
        self.item = item
        self.setProperty("role", "panel")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(self.tr("Doble clic para abrir"))
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 10, 6, 10)
        layout.setSpacing(10)
        layout.addWidget(ColorDot(TYPE_ICONS.get(item.extension, "file"), None), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = label(item.filename, "", wrap=True)
        name.setStyleSheet("font-weight: 600;")
        texts.addWidget(name)
        meta = [formatting.size(item.size)] if item.size >= 0 else []
        if item.page is not None:
            meta.append(self.tr("Página {0}").format(item.page + 1))
        if meta:
            texts.addWidget(label("  ·  ".join(meta), "caption"))
        if item.description and item.description != item.filename:
            texts.addWidget(label(item.description, "muted", wrap=True))
        layout.addLayout(texts, 1)
        more = QToolButton(self)
        more.setIcon(icons.icon("ellipsis"))
        more.setAutoRaise(True)
        more.setToolTip(self.tr("Más opciones"))
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction(icons.icon("external-link"), self.tr("Abrir"), lambda: self.openRequested.emit(item))
        menu.addAction(icons.icon("download"), self.tr("Guardar como..."), lambda: self.saveRequested.emit(item))
        if item.extension in INSERTABLE_EXTENSIONS:
            menu.addAction(icons.icon("box"), self.tr("Insertar como vista 3D"), lambda: self.insert3dRequested.emit(item))
        if item.page is not None:
            menu.addAction(icons.icon("search"), self.tr("Ir a la página"), lambda: self.locateRequested.emit(item))
        menu.addSeparator()
        menu.addAction(icons.icon("trash-2", icons.ROLE_DANGER), self.tr("Quitar del PDF"),
                       lambda: self.removeRequested.emit(item))
        more.setMenu(menu)
        layout.addWidget(more, 0, Qt.AlignmentFlag.AlignTop)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.item.page is not None:
            self.locateRequested.emit(self.item)
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.openRequested.emit(self.item)
            return
        super().mouseDoubleClickEvent(event)


class AttachmentsPanel(QWidget):
    """Lista de los archivos que lleva el PDF (los del documento y los de sus páginas)."""

    countChanged = Signal(int)
    openFiles = Signal(list)
    """Adjuntos que se abren en BlackPDF (PDF, XPS, EPUB o modelos 3D)."""
    locate = Signal(int, QRectF)
    insert3dRequested = Signal(str)
    """Ruta temporal de un adjunto 3D para meterlo como vista 3D."""

    def __init__(self, document: PdfDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self.items: list[core.Attachment] = []
        self._dirty = True
        self.setAcceptDrops(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(8)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)
        self.add_button = Button(self.tr("Adjuntar archivo"), "", "paperclip")
        self.add_button.setToolTip(self.tr("Mete un archivo dentro del PDF. También puedes soltar archivos aquí."))
        self.add_button.clicked.connect(self.add_files)
        layout.addWidget(self.add_button)
        self.save_all_button = Button(self.tr("Guardar todos"), "ghost", "download")
        self.save_all_button.clicked.connect(self.save_all)
        layout.addWidget(self.save_all_button)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self.refresh)
        document.changed.connect(self._on_changed)

    # ---------- actualización ----------
    def _shown(self) -> bool:
        return self.isVisible() and self.width() > 0

    def _on_changed(self, pages) -> None:
        self._dirty = True
        if self._shown():
            self._refresh_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def count(self) -> int:
        if self._dirty:
            try:
                return core.count_attachments(self.document.doc)
            except Exception:
                return 0
        return len(self.items)

    def refresh(self) -> None:
        self._refresh_timer.stop()
        self._dirty = False
        while self.list_layout.count():
            child = self.list_layout.takeAt(0).widget()
            if child is not None:
                child.hide()
                child.setParent(None)
                child.deleteLater()
        try:
            self.items = core.list_attachments(self.document.doc)
        except Exception:
            self.items = []
        self.countChanged.emit(len(self.items))
        self.save_all_button.setVisible(len(self.items) > 1)
        if not self.items:
            empty = QWidget()
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(8, 32, 8, 8)
            glyph = QLabel()
            glyph.setPixmap(icons.pixmap("paperclip", 32, icons.ROLE_MUTED, self.devicePixelRatioF()))
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(glyph)
            text = label(self.tr("Este PDF no lleva archivos adjuntos. Puedes meter dentro planos, modelos, "
                                 "hojas de cálculo o cualquier otro archivo."), "muted", wrap=True)
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(text)
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch(1)
            return
        for item in self.items:
            card = AttachmentCard(item)
            card.openRequested.connect(self.open_attachment)
            card.insert3dRequested.connect(self._insert_3d)
            card.saveRequested.connect(self.save_attachment)
            card.removeRequested.connect(self.remove_attachment)
            card.locateRequested.connect(self._locate)
            self.list_layout.addWidget(card)
        self.list_layout.addStretch(1)

    def _locate(self, item: core.Attachment) -> None:
        if item.page is not None and item.rect is not None:
            x0, y0, x1, y1 = item.rect
            self.locate.emit(item.page, QRectF(x0, y0, x1 - x0, y1 - y0))

    # ---------- acciones ----------
    def open_attachment(self, item: core.Attachment) -> None:
        if item.dangerous:
            box = QMessageBox(self)
            box.setIcon(QMessageBox.Icon.Warning)
            box.setWindowTitle(self.tr("Archivo bloqueado"))
            box.setText("<b>" + self.tr("BlackPDF no abre archivos {0}").format(item.extension) + "</b>")
            box.setInformativeText(self.tr(
                "Es un programa o un script: podría dañar tu equipo si el PDF no es de confianza. "
                "Si conoces a quien te lo envió, guárdalo y ábrelo tú."))
            save = box.addButton(self.tr("Guardar como..."), QMessageBox.ButtonRole.AcceptRole)
            box.addButton(self.tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is save:
                self.save_attachment(item)
            return
        try:
            path = core.save_attachment(self.document.doc, item,
                                        str(_temp_folder() / core.safe_filename(item.filename)))
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        if item.extension in OPEN_IN_BLACKPDF:
            self.openFiles.emit([path])
        elif not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            QMessageBox.information(self, self.tr("Abrir adjunto"), self.tr(
                "No hay ningún programa para abrir archivos {0} en este equipo. "
                "Guárdalo y ábrelo con el programa que uses.").format(item.extension or ""))

    def _insert_3d(self, item: core.Attachment) -> None:
        try:
            path = core.save_attachment(self.document.doc, item,
                                        str(_temp_folder() / core.safe_filename(item.filename)))
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        self.insert3dRequested.emit(path)

    def save_attachment(self, item: core.Attachment) -> None:
        suggested = str(Path(settings().last_directory) / core.safe_filename(item.filename))
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Guardar adjunto"), suggested)
        if not path:
            return
        settings().last_directory = path
        try:
            core.save_attachment(self.document.doc, item, path)
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        Toast.show_message(self.window(), self.tr("Guardado en {0}").format(Path(path).name), kind="success")

    def save_all(self) -> None:
        if not self.items:
            return
        folder = QFileDialog.getExistingDirectory(self, self.tr("Guardar todos los adjuntos en"),
                                                  str(Path(settings().last_directory)))
        if not folder:
            return
        try:
            paths = core.save_all(self.document.doc, self.items, folder)
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        Toast.show_message(self.window(), self.tr("{0} archivos guardados").format(len(paths)),
                           self.tr("Abrir carpeta"), lambda: platform.reveal_in_folder(paths[0]), kind="success")

    def add_files(self, paths: list[str] | None = None) -> None:
        if not paths:
            paths, _ = QFileDialog.getOpenFileNames(self, self.tr("Adjuntar archivos al PDF"),
                                                    settings().last_directory)
            if not paths:
                return
            settings().last_directory = paths[0]

        def operation(doc):
            return [core.add_attachment(doc, path) for path in paths]

        try:
            added = self.document.edit(self.tr("Adjuntar archivos"), operation)
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        count = len(added or [])
        text = self.tr("Archivo adjuntado") if count == 1 else self.tr("{0} archivos adjuntados").format(count)
        Toast.show_message(self.window(), text + ". " + self.tr("Guarda el PDF para conservarlos."), kind="success")

    def remove_attachment(self, item: core.Attachment) -> None:
        try:
            self.document.edit(self.tr("Quitar adjunto"), lambda doc: core.remove_attachment(doc, item))
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        Toast.show_message(self.window(), self.tr("«{0}» quitado del PDF").format(item.filename),
                           self.tr("Deshacer"), self.document.undo)

    # ---------- arrastrar y soltar ----------
    def dragEnterEvent(self, event) -> None:
        if dropped_paths(event):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = dropped_paths(event)
        if paths:
            event.acceptProposedAction()
            self.add_files(paths)
