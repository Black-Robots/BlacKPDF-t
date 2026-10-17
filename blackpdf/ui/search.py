"""Búsqueda de texto incremental con resaltado en el visor."""

from __future__ import annotations

import time

from PySide6.QtCore import QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLineEdit

from ..core.navigation import search_page
from . import icons
from .document import PdfDocument
from .viewer import PdfViewer
from .widgets import IconButton, add_shadow


class SearchController(QObject):
    """Recorre las páginas por tramos para no bloquear la interfaz."""

    progressChanged = Signal(int, bool)
    """(número de resultados, búsqueda terminada)."""
    currentChanged = Signal(int, int)

    def __init__(self, document: PdfDocument, viewer: PdfViewer, parent=None):
        super().__init__(parent)
        self.document = document
        self.viewer = viewer
        self.query = ""
        self.hits: list[tuple[int, int]] = []
        self.current = -1
        self._next_page = 0
        self._order: list[int] = []
        self._timer = QTimer(self)
        self._timer.setInterval(0)
        self._timer.timeout.connect(self._step)
        document.changed.connect(self._on_changed)

    def start(self, query: str) -> None:
        self._timer.stop()
        self.query = query.strip()
        self.hits = []
        self.current = -1
        self.viewer.clear_search()
        if len(self.query) < 1:
            self.progressChanged.emit(0, True)
            return
        count = self.document.page_count
        first = self.viewer.current_page
        self._order = list(range(first, count)) + list(range(0, first))
        self._next_page = 0
        self._timer.start()

    def stop(self) -> None:
        self._timer.stop()
        self.query = ""
        self.hits = []
        self.current = -1
        self.viewer.clear_search()

    def _step(self) -> None:
        deadline = time.perf_counter() + 0.012
        found_any = False
        while self._next_page < len(self._order) and time.perf_counter() < deadline:
            page = self._order[self._next_page]
            self._next_page += 1
            try:
                rects = search_page(self.document.doc[page], self.query)
            except Exception:
                rects = []
            if rects:
                self.viewer.add_search_hits(page, rects)
                self.hits.extend((page, i) for i in range(len(rects)))
                found_any = True
        done = self._next_page >= len(self._order)
        if found_any or done:
            current_hit = self.hits[self.current] if 0 <= self.current < len(self.hits) else None
            self.hits.sort()
            if current_hit is not None:
                self.current = self.hits.index(current_hit)
            elif self.hits:
                self._select(self._first_from(self.viewer.current_page))
            self.progressChanged.emit(len(self.hits), done)
        if done:
            self._timer.stop()

    def _first_from(self, page: int) -> int:
        for position, (hit_page, _) in enumerate(self.hits):
            if hit_page >= page:
                return position
        return 0

    def _select(self, position: int) -> None:
        if not self.hits:
            return
        self.current = position % len(self.hits)
        page, index = self.hits[self.current]
        self.viewer.set_current_hit(page, index)
        self.currentChanged.emit(self.current, len(self.hits))

    def next(self) -> None:
        if self.hits:
            self._select(self.current + 1)

    def previous(self) -> None:
        if self.hits:
            self._select(self.current - 1)

    def _on_changed(self, pages) -> None:
        if self.query:
            QTimer.singleShot(0, lambda: self.start(self.query))


class SearchBar(QFrame):
    """Barra flotante de búsqueda (Ctrl+F)."""

    closed = Signal()
    redactRequested = Signal(str)

    def __init__(self, controller: SearchController, parent=None):
        super().__init__(parent)
        self.controller = controller
        self.setObjectName("FloatingBar")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 6, 6)
        layout.setSpacing(4)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("search", 16, icons.ROLE_MUTED, self.devicePixelRatioF()))
        layout.addWidget(glyph)
        self.edit = QLineEdit(self)
        self.edit.setProperty("variant", "bare")
        self.edit.setPlaceholderText(self.tr("Buscar en el documento"))
        self.edit.setMinimumWidth(220)
        self.edit.installEventFilter(self)
        layout.addWidget(self.edit, 1)
        self.count = QLabel("")
        self.count.setProperty("role", "caption")
        self.count.setMinimumWidth(64)
        self.count.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.count)
        self.prev_button = IconButton("chevron-up", self.tr("Anterior (Mayús+Intro)"), size=28, icon_size=16)
        self.next_button = IconButton("chevron-down", self.tr("Siguiente (Intro)"), size=28, icon_size=16)
        self.close_button = IconButton("x", self.tr("Cerrar (Esc)"), size=28, icon_size=16)
        self.redact_button = IconButton("eye-off", self.tr("Censurar todas las apariciones"), size=28, icon_size=16)
        self.redact_button.clicked.connect(lambda: self.redactRequested.emit(self.controller.query))
        self.redact_button.setEnabled(False)
        for button in (self.prev_button, self.next_button, self.redact_button, self.close_button):
            layout.addWidget(button)
        self.prev_button.clicked.connect(controller.previous)
        self.next_button.clicked.connect(controller.next)
        self.close_button.clicked.connect(self.close_bar)
        add_shadow(self, 24, 6)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(220)
        self._debounce.timeout.connect(lambda: controller.start(self.edit.text()))
        self.edit.textChanged.connect(lambda _: self._debounce.start())
        controller.progressChanged.connect(self._on_progress)
        controller.currentChanged.connect(self._on_current)
        self.setFixedHeight(44)

    def open_bar(self, text: str = "") -> None:
        self.show()
        self.raise_()
        if text:
            self.edit.setText(text)
        self.edit.setFocus()
        self.edit.selectAll()

    def close_bar(self) -> None:
        self._debounce.stop()
        self.controller.stop()
        self.hide()
        self.closed.emit()

    def _on_progress(self, count: int, done: bool) -> None:
        self.redact_button.setEnabled(done and count > 0)
        if not self.controller.query:
            self.count.setText("")
        elif count == 0:
            self.count.setText(self.tr("Sin resultados") if done else self.tr("Buscando..."))
        else:
            self._on_current(self.controller.current, count)

    def _on_current(self, current: int, total: int) -> None:
        self.count.setText(self.tr("{0} de {1}").format(current + 1, total))

    def eventFilter(self, watched, event) -> bool:
        if watched is self.edit and isinstance(event, QKeyEvent) and event.type() == QKeyEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.close_bar()
                return True
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                if self._debounce.isActive():
                    self._debounce.stop()
                    self.controller.start(self.edit.text())
                elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    self.controller.previous()
                else:
                    self.controller.next()
                return True
        return super().eventFilter(watched, event)

    def sizeHint(self) -> QSize:
        return QSize(420, 44)
