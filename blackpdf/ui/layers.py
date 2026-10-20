"""Panel de capas: encender y apagar las capas de un plano (cotas, mobiliario, instalaciones...)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ..core import layers as core
from ..core.errors import BlackPdfError
from . import icons
from .document import PdfDocument
from .errors import show_error
from .widgets import Button, IconButton, Toast, label


class LayerRow(QFrame):
    toggled = Signal(int, bool)

    def __init__(self, layer: core.Layer, parent=None):
        super().__init__(parent)
        self.layer = layer
        layout = QHBoxLayout(self)
        layout.setContentsMargins(4 + 16 * layer.depth, 2, 4, 2)
        layout.setSpacing(8)
        if layer.kind == "label":
            title = label(layer.name, "caption")
            title.setStyleSheet("font-weight: 600;")
            layout.addWidget(title, 1)
            return
        self.setCursor(Qt.CursorShape.PointingHandCursor if layer.toggleable else Qt.CursorShape.ArrowCursor)
        self.eye = IconButton("eye" if layer.visible else "eye-off", "", self, size=26, icon_size=16,
                              role=icons.ROLE_DEFAULT if layer.visible else icons.ROLE_MUTED)
        self.eye.setEnabled(layer.toggleable)
        self.eye.clicked.connect(self._toggle)
        layout.addWidget(self.eye)
        name = QLabel(layer.name or self.tr("Capa sin nombre"), self)
        name.setProperty("role", "" if layer.visible else "muted")
        name.setToolTip(layer.name)
        layout.addWidget(name, 1)
        if layer.locked:
            lock = QLabel(self)
            lock.setPixmap(icons.pixmap("lock", 14, icons.ROLE_MUTED, self.devicePixelRatioF()))
            lock.setToolTip(self.tr("Capa bloqueada por el autor del documento"))
            layout.addWidget(lock)
        if not layer.toggleable:
            tip = self.tr("Capa bloqueada por el autor del documento")
        elif layer.kind == "radiobox":
            tip = self.tr("Solo se puede ver una capa de este grupo a la vez")
        else:
            tip = self.tr("Ocultar capa") if layer.visible else self.tr("Mostrar capa")
        self.eye.setToolTip(tip)
        self.setToolTip(tip)

    def _toggle(self) -> None:
        if self.layer.toggleable:
            self.toggled.emit(self.layer.number, not self.layer.visible)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.layer.kind != "label":
            self._toggle()
            return
        super().mousePressEvent(event)


class LayersPanel(QWidget):
    """Lista de capas del documento con su visibilidad; solo tiene sentido si el PDF trae capas."""

    countChanged = Signal(int)

    def __init__(self, document: PdfDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self._count = -1
        self._dirty = True
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.holder = QWidget(self.scroll)
        self.rows = QVBoxLayout(self.holder)
        self.rows.setContentsMargins(0, 0, 0, 0)
        self.rows.setSpacing(0)
        self.rows.addStretch(1)
        self.scroll.setWidget(self.holder)
        root.addWidget(self.scroll, 1)
        self.empty = label(self.tr("Este documento no tiene capas."), "muted", wrap=True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.empty, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.show_all = Button(self.tr("Mostrar todas"), "ghost", "eye", self)
        self.show_all.clicked.connect(lambda: self.document.set_all_layers_visible(True))
        self.hide_all = Button(self.tr("Ocultar todas"), "ghost", "eye-off", self)
        self.hide_all.clicked.connect(lambda: self.document.set_all_layers_visible(False))
        buttons.addWidget(self.show_all, 1)
        buttons.addWidget(self.hide_all, 1)
        root.addLayout(buttons)
        self.save_default = Button(self.tr("Abrir siempre así"), "", "save", self)
        self.save_default.setToolTip(self.tr(
            "Guarda en el PDF qué capas se ven al abrirlo, en BlackPDF y en otros visores."))
        self.save_default.clicked.connect(self._save_default)
        root.addWidget(self.save_default)
        self.hint = label(self.tr("Ocultar una capa solo cambia lo que ves e imprimes; el archivo no se modifica."),
                          "caption", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.hint)

        document.changed.connect(self._structure_changed)
        document.layersChanged.connect(self.refresh)
        self.refresh()

    def count(self) -> int:
        return max(self._count, 0)

    def _structure_changed(self, pages) -> None:
        # Deshacer o insertar páginas de otro PDF puede traer o quitar capas.
        if pages is None:
            self.refresh()

    def refresh(self) -> None:
        try:
            layers = self.document.layers()
        except Exception:
            layers = []
        while self.rows.count() > 1:
            item = self.rows.takeAt(0)
            if item.widget() is not None:
                item.widget().deleteLater()
        for position, layer in enumerate(layers):
            row = LayerRow(layer, self.holder)
            row.toggled.connect(self.document.set_layer_visible)
            self.rows.insertWidget(position, row)
        has = bool(layers)
        self.scroll.setVisible(has)
        self.empty.setVisible(not has)
        checkboxes = [layer for layer in layers if layer.toggleable and layer.kind == "checkbox"]
        for button in (self.show_all, self.hide_all):
            button.setVisible(bool(checkboxes))
        self.show_all.setEnabled(any(not layer.visible for layer in checkboxes))
        self.hide_all.setEnabled(any(layer.visible for layer in checkboxes))
        try:
            default = core.is_default(self.document.doc)
        except Exception:
            default = True
        self.save_default.setVisible(has)
        self.hint.setVisible(has)
        self.save_default.setEnabled(not default)
        count = sum(1 for layer in layers if layer.kind != "label")
        if count != self._count:
            self._count = count
            self.countChanged.emit(count)

    def _save_default(self) -> None:
        try:
            self.document.edit(self.tr("Capas al abrir"), lambda doc: core.save_as_default(doc) or True)
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        except Exception as exc:
            show_error(self, exc, self.tr("Capas al abrir"))
            return
        Toast.show_message(self.window(), self.tr("Guarda el PDF para que se abra con estas capas."), kind="success")
