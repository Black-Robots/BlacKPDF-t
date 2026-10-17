"""Vista dividida: un segundo visor junto al principal.

Puede mostrar el mismo documento en otro punto, otro documento abierto en
una pestaña o un PDF cualquiera del disco, con desplazamiento sincronizado
opcional para comparar versiones.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import QComboBox, QFileDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..core.errors import BlackPdfError, PasswordRequiredError, WrongPasswordError
from .dialogs.common import ask_password
from .document import PdfDocument
from .errors import show_error
from .interaction import SelectTool
from .render import PageRenderer
from .settings import settings
from .viewer import PdfViewer
from .widgets import IconButton, open_filter, separator

DocumentsProvider = Callable[[], list[tuple[str, PdfDocument]]]


class SplitPane(QWidget):
    closeRequested = Signal()
    syncChanged = Signal(bool)

    def __init__(self, primary: PdfViewer, own: PdfDocument, provider: DocumentsProvider | None, parent=None):
        super().__init__(parent)
        self.primary = primary
        self.own = own
        self.provider = provider
        self.model: PdfDocument | None = None
        self._owned: PdfDocument | None = None
        self.viewer: PdfViewer | None = None
        self.renderer: PageRenderer | None = None
        self._sync = False
        self._page_delta = 0
        self._syncing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QFrame(self)
        bar.setObjectName("PaneBar")
        bar.setFixedHeight(44)
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(10, 0, 6, 0)
        bar_layout.setSpacing(4)
        self.chooser = QComboBox(bar)
        self.chooser.setMinimumWidth(180)
        self.chooser.setMaximumWidth(300)
        self.chooser.activated.connect(self._chosen)
        bar_layout.addWidget(self.chooser, 1)
        self.page_label = QLabel("", bar)
        self.page_label.setProperty("role", "caption")
        self.page_label.setMinimumWidth(56)
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        bar_layout.addWidget(self.page_label)
        bar_layout.addWidget(separator(True))
        self.zoom_out = IconButton("zoom-out", self.tr("Alejar"), bar, size=30, icon_size=16)
        self.zoom_in = IconButton("zoom-in", self.tr("Acercar"), bar, size=30, icon_size=16)
        self.fit = IconButton("move-horizontal", self.tr("Ajustar al ancho"), bar, size=30, icon_size=16)
        for button in (self.zoom_out, self.zoom_in, self.fit):
            bar_layout.addWidget(button)
        self.zoom_out.clicked.connect(lambda: self.viewer and self.viewer.zoom_out())
        self.zoom_in.clicked.connect(lambda: self.viewer and self.viewer.zoom_in())
        self.fit.clicked.connect(lambda: self.viewer and self.viewer.set_fit_mode("width"))
        bar_layout.addWidget(separator(True))
        self.sync_button = IconButton("link-2", self.tr("Desplazamiento sincronizado"), bar, checkable=True, size=30, icon_size=16)
        self.sync_button.toggled.connect(self.set_sync)
        bar_layout.addWidget(self.sync_button)
        self.close_button = IconButton("x", self.tr("Cerrar la vista dividida"), bar, size=30, icon_size=16)
        self.close_button.clicked.connect(self.closeRequested)
        bar_layout.addWidget(self.close_button)
        layout.addWidget(bar)
        self.holder = QVBoxLayout()
        self.holder.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(self.holder, 1)
        primary.verticalScrollBar().valueChanged.connect(self._primary_scrolled)
        primary.zoomChanged.connect(lambda _: self._primary_scrolled())

    # ---------- documento ----------
    def _entries(self) -> list[tuple[str, PdfDocument]]:
        entries = [(self.tr("Este documento"), self.own)]
        if self.provider is not None:
            for name, model in self.provider():
                if model is not self.own:
                    entries.append((name, model))
        if self._owned is not None:
            entries.append((self._owned.display_name, self._owned))
        return entries

    def refresh_chooser(self) -> None:
        self.chooser.blockSignals(True)
        self.chooser.clear()
        for name, model in self._entries():
            self.chooser.addItem(name, model)
        self.chooser.insertSeparator(self.chooser.count())
        self.chooser.addItem(self.tr("Abrir otro PDF..."), None)
        for index in range(self.chooser.count()):
            if self.chooser.itemData(index) is self.model:
                self.chooser.setCurrentIndex(index)
        self.chooser.blockSignals(False)

    def _chosen(self, index: int) -> None:
        model = self.chooser.itemData(index)
        if model is None:
            self._open_file()
        elif model is not self.model:
            self.set_document(model)

    def _open_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Abrir PDF en la vista dividida"),
                                              settings().last_directory, open_filter())
        if not path:
            self.refresh_chooser()
            return
        settings().last_directory = path
        password = None
        retry = False
        while True:
            try:
                model = PdfDocument.open(path, password)
                break
            except PasswordRequiredError:
                pass
            except WrongPasswordError:
                retry = True
            except BlackPdfError as exc:
                show_error(self, exc)
                self.refresh_chooser()
                return
            password = ask_password(self, path, retry)
            if password is None:
                self.refresh_chooser()
                return
        if self._owned is not None and self._owned is not self.model:
            self._owned.doc.close()
        self._owned = model
        self.set_document(model)

    def set_document(self, model: PdfDocument, page: int | None = None) -> None:
        if self.viewer is not None:
            self.viewer.setParent(None)
            self.viewer.deleteLater()
            self.viewer = None
        if self.renderer is not None:
            self.renderer.deleteLater()
        self.model = model
        self.renderer = PageRenderer(model, self)
        viewer = PdfViewer(model, self.renderer, self)
        viewer.set_tool(SelectTool(viewer))
        viewer.set_night_mode(self.primary.night_mode)
        viewer.currentPageChanged.connect(self._update_page_label)
        viewer.verticalScrollBar().valueChanged.connect(self._secondary_scrolled)
        model.changed.connect(self._update_page_label, Qt.ConnectionType.UniqueConnection)
        self.holder.addWidget(viewer)
        self.viewer = viewer
        viewer.set_fit_mode("auto")
        if page is not None:
            viewer.go_to_page(page, smooth=False)
        self.refresh_chooser()
        self._update_page_label()
        if self._sync:
            self._capture_delta()

    def document_closing(self, model: PdfDocument) -> None:
        """Otra pestaña se cierra: si se estaba mostrando, se vuelve a este documento."""
        if model is self.model:
            self.set_document(self.own)
        else:
            self.refresh_chooser()

    def shutdown(self) -> None:
        if self._owned is not None:
            try:
                self._owned.doc.close()
            except Exception:
                pass
            self._owned = None

    def _update_page_label(self, *_):
        if self.viewer is None or self.model is None:
            return
        self.page_label.setText(f"{self.viewer.current_page + 1} / {self.model.page_count}")

    def set_night_mode(self, enabled: bool) -> None:
        if self.viewer is not None:
            self.viewer.set_night_mode(enabled)

    # ---------- sincronización ----------
    def _capture_delta(self) -> None:
        if self.viewer is None:
            return
        self._page_delta = self.viewer.position()[0] - self.primary.position()[0]

    def set_sync(self, enabled: bool) -> None:
        self._sync = enabled
        if self.sync_button.isChecked() != enabled:
            self.sync_button.setChecked(enabled)
        self.sync_button.set_icon_name("link-2" if enabled else "link-2-off")
        if enabled:
            self._capture_delta()
        self.syncChanged.emit(enabled)

    def _primary_scrolled(self, *_):
        if not self._sync or self._syncing or self.viewer is None:
            return
        self._syncing = True
        page, offset = self.primary.position()
        self.viewer.set_position(page + self._page_delta, offset)
        self._syncing = False

    def _secondary_scrolled(self, *_):
        if not self._sync or self._syncing or self.viewer is None:
            return
        self._syncing = True
        page, offset = self.viewer.position()
        self.primary.set_position(page - self._page_delta, offset)
        self._syncing = False

    def sizeHint(self) -> QSize:
        return QSize(520, 600)
