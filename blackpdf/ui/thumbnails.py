"""Panel de miniaturas: navegar y organizar páginas.

Reordenar arrastrando, selección múltiple con Ctrl/Shift, y soltar PDF o
imágenes desde el explorador para insertarlos en esa posición.
"""

from __future__ import annotations

import json

from PySide6.QtCore import (
    QAbstractListModel,
    QByteArray,
    QEvent,
    QMimeData,
    QModelIndex,
    QPoint,
    QRect,
    QRectF,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QDrag, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QAbstractItemView, QListView, QStyle, QStyledItemDelegate, QStyleOptionViewItem

from ..core.document import OPENABLE_EXTENSIONS
from . import icons, theme
from .document import PdfDocument
from .render import PageRenderer
from .widgets import dropped_paths

MIME_PAGES = "application/x-blackpdf-pages"
THUMB_BOX = 132
LABEL_HEIGHT = 22
PADDING = 10


class ThumbnailModel(QAbstractListModel):
    movePages = Signal(list, int)
    insertFiles = Signal(list, int)

    def __init__(self, document: PdfDocument, renderer: PageRenderer, parent=None):
        super().__init__(parent)
        self.document = document
        self.renderer = renderer
        self.night = False
        self._uid = id(self)
        document.changed.connect(self._on_changed)
        renderer.thumbReady.connect(self._on_thumb)

    def rowCount(self, parent=QModelIndex()) -> int:
        return 0 if parent.isValid() else self.document.page_count

    def data(self, index: QModelIndex, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = index.row()
        if role == Qt.ItemDataRole.DisplayRole:
            return self.document.page_label(row)
        if role == Qt.ItemDataRole.DecorationRole:
            image = self.renderer.thumbnail(row, self.night)
            if image is None:
                self.renderer.request_thumbnail(row, self.night)
            return image
        if role == Qt.ItemDataRole.UserRole:
            return self.document.page_size(row) if row < self.document.page_count else None
        return None

    def flags(self, index: QModelIndex):
        base = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsDragEnabled
        if not index.isValid():
            return Qt.ItemFlag.ItemIsDropEnabled
        return base

    def supportedDropActions(self):
        return Qt.DropAction.MoveAction | Qt.DropAction.CopyAction

    def supportedDragActions(self):
        return Qt.DropAction.MoveAction

    def mimeTypes(self) -> list[str]:
        return [MIME_PAGES, "text/uri-list"]

    def mimeData(self, indexes) -> QMimeData:
        rows = sorted({i.row() for i in indexes})
        mime = QMimeData()
        mime.setData(MIME_PAGES, QByteArray(json.dumps({"uid": self._uid, "rows": rows}).encode()))
        return mime

    def canDropMimeData(self, data, action, row, column, parent) -> bool:
        return data.hasFormat(MIME_PAGES) or data.hasUrls()

    def dropMimeData(self, data: QMimeData, action, row: int, column: int, parent: QModelIndex) -> bool:
        target = row if row >= 0 else (parent.row() if parent.isValid() else self.rowCount())
        if data.hasFormat(MIME_PAGES):
            try:
                payload = json.loads(bytes(data.data(MIME_PAGES)).decode())
            except ValueError:
                return False
            if payload.get("uid") != self._uid:
                return False
            self.movePages.emit(payload.get("rows", []), target)
            return False  # el movimiento lo hace el documento, no la vista
        if data.hasUrls():
            paths = [u.toLocalFile() for u in data.urls() if u.isLocalFile()]
            paths = [p for p in paths if p.lower().endswith(OPENABLE_EXTENSIONS)]
            if paths:
                self.insertFiles.emit(paths, target)
        return False

    def _on_changed(self, pages) -> None:
        if pages is None:
            self.beginResetModel()
            self.endResetModel()
            return
        for page in pages:
            index = self.index(page)
            self.dataChanged.emit(index, index)

    def _on_thumb(self, page: int) -> None:
        if page < self.rowCount():
            index = self.index(page)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def set_night(self, night: bool) -> None:
        if night != self.night:
            self.night = night
            self.beginResetModel()
            self.endResetModel()


ACTION_SIZE = 28


class ThumbnailDelegate(QStyledItemDelegate):
    """Dibuja una miniatura con sombra, número y estado (actual, seleccionada).

    En la vista de organizar muestra además botones de girar y eliminar al
    pasar el ratón.
    """

    rotateClicked = Signal(int, int)
    deleteClicked = Signal(int)

    def __init__(self, view: "ThumbnailView", box: int = THUMB_BOX, actions: bool = False, grid: bool = False):
        super().__init__(view)
        self.view = view
        self.box = box
        self.actions = actions
        self.grid = grid

    def sizeHint(self, option, index) -> QSize:
        height = self.box + LABEL_HEIGHT + 2 * PADDING
        if self.grid:
            return QSize(int(self.box * 0.82) + 2 * PADDING, height)
        return QSize(max(option.rect.width(), self.box + 2 * PADDING), height)

    def _page_rect(self, rect: QRect, index: QModelIndex) -> QRectF:
        size = index.data(Qt.ItemDataRole.UserRole)
        box = QRectF(rect.left() + PADDING, rect.top() + PADDING, rect.width() - 2 * PADDING, self.box)
        if size is not None and size.width() > 0 and size.height() > 0:
            ratio = size.width() / size.height()
            if ratio >= box.width() / box.height():
                w, h = box.width(), box.width() / ratio
            else:
                w, h = box.height() * ratio, box.height()
        else:
            w, h = box.width() * 0.7, box.height()
        return QRectF(box.center().x() - w / 2, box.top() + (box.height() - h) / 2, w, h)

    def action_rects(self, rect: QRect, index: QModelIndex) -> dict[str, QRectF]:
        page = self._page_rect(rect, index)
        y = page.top() + 6
        right = page.right() - 6
        names = ("delete", "rotate-cw", "rotate-ccw")
        result = {}
        for position, name in enumerate(names):
            x = right - (position + 1) * ACTION_SIZE - position * 4
            result[name] = QRectF(x, y, ACTION_SIZE, ACTION_SIZE)
        return result

    def editorEvent(self, event, model, option, index) -> bool:
        if not self.actions or event.type() not in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                                                    QEvent.Type.MouseButtonDblClick):
            return False
        pos = event.position()
        for name, area in self.action_rects(option.rect, index).items():
            if area.contains(pos):
                if event.type() == QEvent.Type.MouseButtonRelease:
                    if name == "delete":
                        self.deleteClicked.emit(index.row())
                    else:
                        self.rotateClicked.emit(index.row(), 90 if name == "rotate-cw" else -90)
                return True
        return False

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        p = theme.current()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        current = index.row() == self.view.current_page

        box = QRectF(rect.left() + PADDING, rect.top() + PADDING, rect.width() - 2 * PADDING, self.box)
        page_rect = self._page_rect(rect, index)

        if selected or hovered:
            frame = QRectF(rect).adjusted(3, 3, -3, -3)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(p.qcolor("accent_soft") if selected else p.qcolor("hover"))
            painter.drawRoundedRect(frame, theme.RADIUS, theme.RADIUS)

        shadow = p.qcolor("page_shadow")
        shadow.setAlphaF(shadow.alphaF() * 0.6)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(shadow)
        painter.drawRoundedRect(page_rect.adjusted(-1, 0, 1, 2), 2, 2)
        image = index.data(Qt.ItemDataRole.DecorationRole)
        painter.setBrush(p.qcolor("night_page") if self.view.night else QColor("#FFFFFF"))
        painter.drawRect(page_rect)
        if image is not None:
            painter.drawImage(page_rect, image)

        if selected or current:
            pen = QPen(p.qcolor("accent"), 2 if selected else 1.5)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(page_rect.adjusted(-2, -2, 2, 2), 3, 3)

        font = QFont(option.font)
        font.setPixelSize(12)
        font.setWeight(QFont.Weight.DemiBold if current or selected else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(p.qcolor("text") if current or selected else p.qcolor("text_muted"))
        label_rect = QRect(rect.left(), int(box.bottom()) + 4, rect.width(), LABEL_HEIGHT - 4)
        painter.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignVCenter, index.data())
        if self.actions and hovered:
            self._paint_actions(painter, option, index)
        painter.restore()

    def _paint_actions(self, painter: QPainter, option, index) -> None:
        p = theme.current()
        cursor = self.view.viewport().mapFromGlobal(QCursor.pos())
        dpr = self.view.devicePixelRatioF()
        for name, area in self.action_rects(option.rect, index).items():
            hot = area.contains(cursor.toPointF())
            painter.setPen(QPen(p.qcolor("border_strong"), 1))
            if hot and name == "delete":
                painter.setBrush(p.qcolor("danger"))
            else:
                painter.setBrush(p.qcolor("hover") if hot else p.qcolor("surface"))
            painter.drawEllipse(area)
            role = icons.ROLE_ON_ACCENT if hot and name == "delete" else icons.ROLE_TEXT
            glyph = icons.pixmap("trash-2" if name == "delete" else name, 16, role, dpr)
            painter.drawPixmap(int(area.center().x() - 8), int(area.center().y() - 8), glyph)


class ThumbnailView(QListView):
    pageActivated = Signal(int)
    contextMenuAt = Signal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_page = 0
        self.night = False
        self.setItemDelegate(ThumbnailDelegate(self))
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(False)
        self._drop_row: int | None = None
        self._dragging_rows: list[int] = []
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMouseTracking(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.contextMenuAt)
        self.clicked.connect(lambda index: self.pageActivated.emit(index.row()))
        self.setSpacing(0)
        self.setFrameShape(QListView.Shape.NoFrame)

    def selected_pages(self) -> list[int]:
        return sorted(index.row() for index in self.selectionModel().selectedIndexes())

    def set_current_page(self, page: int) -> None:
        if page == self.current_page:
            return
        self.current_page = page
        index = self.model().index(page, 0) if self.model() else QModelIndex()
        if index.isValid():
            if len(self.selected_pages()) <= 1:
                self.selectionModel().setCurrentIndex(
                    index, self.selectionModel().SelectionFlag.ClearAndSelect
                )
            self.scrollTo(index, QAbstractItemView.ScrollHint.EnsureVisible)
        self.viewport().update()

    def select_pages(self, pages: list[int]) -> None:
        model = self.model()
        selection = self.selectionModel()
        selection.clearSelection()
        for page in pages:
            if 0 <= page < model.rowCount():
                selection.select(model.index(page, 0), selection.SelectionFlag.Select)
        if pages:
            self.scrollTo(model.index(pages[0], 0))

    # ---------- arrastrar y soltar ----------
    def insertion_row(self, pos: QPoint) -> int:
        """Posición de inserción (0..n) según dónde está el cursor."""
        model = self.model()
        count = model.rowCount() if model else 0
        if count == 0:
            return 0
        index = self.indexAt(pos)
        if self.viewMode() == QListView.ViewMode.IconMode:
            if not index.isValid():
                for dx in (-16, 16, -32, 32):
                    index = self.indexAt(pos + QPoint(dx, 0))
                    if index.isValid():
                        break
            if not index.isValid():
                last = self.visualRect(model.index(count - 1, 0))
                return count if pos.y() > last.top() else 0
            rect = self.visualRect(index)
            return index.row() + (1 if pos.x() > rect.center().x() else 0)
        if not index.isValid():
            first = self.visualRect(model.index(0, 0))
            return 0 if pos.y() < first.top() else count
        rect = self.visualRect(index)
        return index.row() + (1 if pos.y() > rect.center().y() else 0)

    def startDrag(self, actions) -> None:
        rows = self.selected_pages()
        if not rows:
            return
        model = self.model()
        mime = model.mimeData([model.index(r, 0) for r in rows])
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(self._drag_pixmap(rows))
        drag.setHotSpot(QPoint(40, 30))
        self._dragging_rows = rows
        drag.exec(Qt.DropAction.MoveAction)
        self._dragging_rows = []
        self._clear_indicator()

    def _drag_pixmap(self, rows: list[int]) -> QPixmap:
        dpr = self.devicePixelRatioF()
        width, height = 96, 120
        pixmap = QPixmap(int((width + 16) * dpr), int((height + 16) * dpr))
        pixmap.setDevicePixelRatio(dpr)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p = theme.current()
        image = self.model().data(self.model().index(rows[0], 0), Qt.ItemDataRole.DecorationRole)
        layers = min(len(rows), 3)
        for layer in range(layers - 1, -1, -1):
            offset = layer * 4
            rect = QRectF(4 + offset, 4 + offset, width - 8, height - 8)
            painter.setPen(QPen(p.qcolor("accent"), 1.5))
            painter.setBrush(QColor("#FFFFFF"))
            painter.setOpacity(0.92 if layer == 0 else 0.6)
            painter.drawRoundedRect(rect, 3, 3)
            if layer == 0 and image is not None:
                ratio = image.width() / max(image.height(), 1)
                box_w, box_h = rect.width() - 4, rect.height() - 4
                w, h = (box_w, box_w / ratio) if ratio > box_w / box_h else (box_h * ratio, box_h)
                target = QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
                painter.drawImage(target, image)
        painter.setOpacity(1.0)
        if len(rows) > 1:
            badge = QRectF(width - 22, height - 18, 30, 22)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(p.qcolor("accent"))
            painter.drawRoundedRect(badge, 11, 11)
            painter.setPen(p.qcolor("accent_text"))
            font = painter.font()
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.drawText(badge, Qt.AlignmentFlag.AlignCenter, str(len(rows)))
        painter.end()
        return pixmap

    def _accepts(self, event) -> bool:
        mime = event.mimeData()
        return mime.hasFormat(MIME_PAGES) or bool(dropped_paths(event, OPENABLE_EXTENSIONS))

    def dragEnterEvent(self, event) -> None:
        if self._accepts(event):
            event.setDropAction(Qt.DropAction.MoveAction if event.mimeData().hasFormat(MIME_PAGES) else Qt.DropAction.CopyAction)
            event.accept()
            self._set_indicator(self.insertion_row(event.position().toPoint()))
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        if not self._accepts(event):
            event.ignore()
            return
        pos = event.position().toPoint()
        margin = 36
        bar = self.verticalScrollBar()
        if pos.y() < margin:
            bar.setValue(bar.value() - 14)
        elif pos.y() > self.viewport().height() - margin:
            bar.setValue(bar.value() + 14)
        event.setDropAction(Qt.DropAction.MoveAction if event.mimeData().hasFormat(MIME_PAGES) else Qt.DropAction.CopyAction)
        event.accept()
        self._set_indicator(self.insertion_row(pos))

    def dragLeaveEvent(self, event) -> None:
        self._clear_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:
        target = self.insertion_row(event.position().toPoint())
        self._clear_indicator()
        mime = event.mimeData()
        model = self.model()
        if mime.hasFormat(MIME_PAGES):
            try:
                payload = json.loads(bytes(mime.data(MIME_PAGES)).decode())
            except ValueError:
                event.ignore()
                return
            if payload.get("uid") == model._uid:
                event.setDropAction(Qt.DropAction.MoveAction)
                event.accept()
                model.movePages.emit(payload.get("rows", []), target)
                return
            event.ignore()
            return
        paths = dropped_paths(event, OPENABLE_EXTENSIONS)
        if paths:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            model.insertFiles.emit(paths, target)
            return
        event.ignore()

    def _set_indicator(self, row: int) -> None:
        if row != self._drop_row:
            self._drop_row = row
            self.viewport().update()

    def _clear_indicator(self) -> None:
        if self._drop_row is not None:
            self._drop_row = None
            self.viewport().update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        if self._drop_row is None or self.model() is None:
            return
        count = self.model().rowCount()
        if count == 0:
            y = 4
            rect = self.viewport().rect()
        elif self._drop_row >= count:
            rect = self.visualRect(self.model().index(count - 1, 0))
            y = rect.bottom() + 1
        else:
            rect = self.visualRect(self.model().index(self._drop_row, 0))
            y = rect.top()
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = theme.color("accent")
        painter.setPen(QPen(color, 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        if self.viewMode() == QListView.ViewMode.IconMode and count:
            if self._drop_row >= count:
                x = rect.right() + 2
            else:
                x = rect.left() - 2
            top, bottom = rect.top() + 10, rect.bottom() - 10
            painter.drawLine(x, top, x, bottom)
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QRectF(x - 5, top - 5, 10, 10))
            painter.drawEllipse(QRectF(x - 5, bottom - 5, 10, 10))
            return
        left, right = rect.left() + 14, rect.right() - 14
        painter.drawLine(left, y, right, y)
        painter.setBrush(color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(QRectF(left - 5, y - 5, 10, 10))
        painter.drawEllipse(QRectF(right - 5, y - 5, 10, 10))

    def keyPressEvent(self, event) -> None:
        super().keyPressEvent(event)
        if event.key() in (Qt.Key.Key_Up, Qt.Key.Key_Down, Qt.Key.Key_Home, Qt.Key.Key_End,
                           Qt.Key.Key_PageUp, Qt.Key.Key_PageDown):
            index = self.currentIndex()
            if index.isValid():
                self.pageActivated.emit(index.row())


class OrganizerView(ThumbnailView):
    """Cuadrícula grande de páginas para organizar el documento entero."""

    rotateRequested = Signal(list, int)
    deleteRequested = Signal(list)
    openPage = Signal(int)
    MIN_BOX, MAX_BOX = 120, 360

    def __init__(self, parent=None, box: int = 210):
        super().__init__(parent)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setWrapping(True)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        # setMovement(Static) desactiva el arrastre: se reactiva, lo gestiona esta clase.
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)
        self.setSpacing(6)
        self.setUniformItemSizes(True)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(40)
        self.organizer_delegate = ThumbnailDelegate(self, box=box, actions=True, grid=True)
        self.setItemDelegate(self.organizer_delegate)
        self.organizer_delegate.rotateClicked.connect(self._rotate_clicked)
        self.organizer_delegate.deleteClicked.connect(self._delete_clicked)
        self.doubleClicked.connect(lambda index: self.openPage.emit(index.row()))
        try:
            self.clicked.disconnect()
        except (RuntimeError, TypeError):
            pass

    def _targets(self, row: int) -> list[int]:
        selected = self.selected_pages()
        return selected if row in selected and len(selected) > 1 else [row]

    def _rotate_clicked(self, row: int, angle: int) -> None:
        self.rotateRequested.emit(self._targets(row), angle)

    def _delete_clicked(self, row: int) -> None:
        self.deleteRequested.emit(self._targets(row))

    def set_box(self, box: int) -> None:
        box = max(self.MIN_BOX, min(self.MAX_BOX, box))
        if box != self.organizer_delegate.box:
            self.organizer_delegate.box = box
            self._center()
            self.scheduleDelayedItemsLayout()
            self.viewport().update()

    def _center(self) -> None:
        """Centra la cuadrícula repartiendo el espacio sobrante a ambos lados."""
        item = self.organizer_delegate.sizeHint(self.viewOptions() if hasattr(self, "viewOptions") else None, QModelIndex())
        cell = item.width() + self.spacing()
        scrollbar = self.verticalScrollBar().sizeHint().width()
        available = max(cell, self.width() - scrollbar - 4)
        columns = max(1, (available - self.spacing()) // cell)
        if self.model() is not None:
            columns = max(1, min(columns, self.model().rowCount()))
        side = max(0, (available - columns * cell - self.spacing()) // 2)
        if self.viewportMargins().left() != side:
            self.setViewportMargins(side, 8, 0, 8)

    def resizeEvent(self, event) -> None:
        self._center()
        super().resizeEvent(event)

    def wheelEvent(self, event) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120
            self.set_box(int(self.organizer_delegate.box * (1.1 ** steps)))
            event.accept()
            return
        super().wheelEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        self.viewport().update()
