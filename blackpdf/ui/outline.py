"""Panel de índice (marcadores del documento)."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QLabel, QStackedLayout, QTreeWidget, QTreeWidgetItem, QWidget

from ..core.navigation import OutlineItem, get_outline
from .document import PdfDocument


class OutlinePanel(QWidget):
    pageRequested = Signal(int)

    def __init__(self, document: PdfDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self._stack = QStackedLayout(self)
        self.tree = QTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(14)
        self.tree.setUniformRowHeights(True)
        self.tree.setAnimated(True)
        self.tree.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tree.itemClicked.connect(self._activate)
        self.tree.itemActivated.connect(self._activate)
        self.empty = QLabel(self.tr("Este documento no tiene índice."))
        self.empty.setProperty("role", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setWordWrap(True)
        self._stack.addWidget(self.tree)
        self._stack.addWidget(self.empty)
        self._dirty = True
        document.changed.connect(self._mark_dirty)

    def _mark_dirty(self, pages) -> None:
        if pages is None:
            self._dirty = True
            if self.isVisible():
                self.refresh()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        self.tree.clear()
        try:
            roots = get_outline(self.document.doc)
        except Exception:
            roots = []
        for item in roots:
            self.tree.addTopLevelItem(self._build(item))
        if roots and len(roots) <= 12:
            self.tree.expandToDepth(0)
        self._stack.setCurrentWidget(self.tree if roots else self.empty)

    def _build(self, item: OutlineItem) -> QTreeWidgetItem:
        node = QTreeWidgetItem([item.title])
        node.setData(0, Qt.ItemDataRole.UserRole, item.page)
        node.setToolTip(0, item.title)
        for child in item.children:
            node.addChild(self._build(child))
        return node

    def _activate(self, node: QTreeWidgetItem) -> None:
        page = node.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(page, int) and page >= 0:
            self.pageRequested.emit(page)
