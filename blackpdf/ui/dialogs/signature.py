"""Firma: dibujarla una vez, guardarla y reutilizarla."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...core.annotations import normalize_strokes
from .. import theme
from ..settings import settings
from ..widgets import Button, ColorButton, IconButton, label

SIGNATURE_COLORS = ("#111114", "#1B2A6B", "#0090FF", "#E5484D")


def _paint_strokes(painter: QPainter, strokes, rect: QRectF, color: QColor, width: float) -> None:
    """Dibuja trazos normalizados (0-1) centrados en `rect`."""
    if not strokes:
        return
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    w_unit = max(max(xs), 1e-3)
    h_unit = max(max(ys), 1e-3)
    scale = min(rect.width() / w_unit, rect.height() / h_unit)
    off_x = rect.left() + (rect.width() - w_unit * scale) / 2
    off_y = rect.top() + (rect.height() - h_unit * scale) / 2
    pen = QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    for stroke in strokes:
        if not stroke:
            continue
        path = QPainterPath(QPointF(off_x + stroke[0][0] * scale, off_y + stroke[0][1] * scale))
        if len(stroke) == 1:
            path.lineTo(QPointF(off_x + stroke[0][0] * scale + 0.1, off_y + stroke[0][1] * scale))
        for x, y in stroke[1:]:
            path.lineTo(QPointF(off_x + x * scale, off_y + y * scale))
        painter.drawPath(path)


class SignaturePad(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.strokes: list[list[tuple[float, float]]] = []
        self.color = QColor("#1B2A6B")
        self.setMinimumSize(520, 200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setCursor(Qt.CursorShape.CrossCursor)

    def clear(self) -> None:
        self.strokes = []
        self.update()
        self.changed.emit()

    def undo(self) -> None:
        if self.strokes:
            self.strokes.pop()
            self.update()
            self.changed.emit()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            p = event.position()
            self.strokes.append([(p.x(), p.y())])
            self.update()

    def mouseMoveEvent(self, event) -> None:
        if event.buttons() & Qt.MouseButton.LeftButton and self.strokes:
            p = event.position()
            last = self.strokes[-1][-1]
            if abs(p.x() - last[0]) + abs(p.y() - last[1]) >= 1.5:
                self.strokes[-1].append((p.x(), p.y()))
                self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.changed.emit()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(theme.color("border_strong"), 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        baseline_y = rect.bottom() - rect.height() * 0.28
        guide = QPen(QColor("#C9C9D1"), 1, Qt.PenStyle.DashLine)
        painter.setPen(guide)
        painter.drawLine(QPointF(rect.left() + 32, baseline_y), QPointF(rect.right() - 32, baseline_y))
        if not self.strokes:
            painter.setPen(QColor("#9A9AA5"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, self.tr("Firma aquí con el ratón o el lápiz"))
            return
        pen = QPen(self.color, 2.6)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        for stroke in self.strokes:
            path = QPainterPath(QPointF(*stroke[0]))
            if len(stroke) == 1:
                path.lineTo(QPointF(stroke[0][0] + 0.1, stroke[0][1]))
            for point in stroke[1:]:
                path.lineTo(QPointF(*point))
            painter.drawPath(path)

    def normalized(self) -> list[list[list[float]]]:
        return [[[x, y] for x, y in stroke] for stroke in normalize_strokes(self.strokes)]


class SavedSignature(QToolButton):
    def __init__(self, strokes, parent=None):
        super().__init__(parent)
        self.strokes = strokes
        self.setCheckable(True)
        self.setFixedSize(QSize(168, 72))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        painter.setPen(QPen(p.qcolor("accent") if self.isChecked() else p.qcolor("border_strong"), 2 if self.isChecked() else 1))
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        _paint_strokes(painter, self.strokes, rect.adjusted(14, 10, -14, -10), QColor("#1B2A6B"), 1.6)


class SignatureDialog(QDialog):
    """Elegir una firma guardada o dibujar una nueva."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Firma"))
        self.setModal(True)
        self._chosen: list | None = None
        self._saved = settings().signatures()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(label(self.tr("Tu firma"), "title"))
        layout.addWidget(label(
            self.tr("Dibuja tu firma una vez y guárdala. Después podrás estamparla en cualquier documento."),
            "muted", wrap=True))

        self.saved_row = QHBoxLayout()
        self.saved_row.setSpacing(8)
        self._saved_buttons: list[SavedSignature] = []
        self._saved_holder = QWidget()
        self._saved_holder.setLayout(self.saved_row)
        layout.addWidget(self._saved_holder)

        self.pad = SignaturePad()
        layout.addWidget(self.pad, 1)

        tools = QHBoxLayout()
        tools.setSpacing(6)
        tools.addWidget(QLabel(self.tr("Color de la vista previa")))
        self.color = ColorButton("#1B2A6B", SIGNATURE_COLORS)
        self.color.colorChanged.connect(self._set_color)
        tools.addWidget(self.color)
        tools.addStretch(1)
        undo = IconButton("undo-2", self.tr("Deshacer trazo"))
        undo.clicked.connect(self.pad.undo)
        clear = Button(self.tr("Borrar"), "ghost", "eraser")
        clear.clicked.connect(self.pad.clear)
        tools.addWidget(undo)
        tools.addWidget(clear)
        layout.addLayout(tools)

        buttons = QHBoxLayout()
        self.delete_button = Button(self.tr("Eliminar guardada"), "danger", "trash-2")
        self.delete_button.clicked.connect(self._delete_selected)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        cancel = Button(self.tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        self.use_button = Button(self.tr("Usar firma"), "primary", "check")
        self.use_button.clicked.connect(self._use)
        buttons.addWidget(cancel)
        buttons.addWidget(self.use_button)
        layout.addLayout(buttons)

        self.pad.changed.connect(self._refresh_state)
        self._rebuild_saved()
        self._refresh_state()

    def _set_color(self, value: str) -> None:
        self.pad.color = QColor(value)
        self.pad.update()

    def _rebuild_saved(self) -> None:
        for button in self._saved_buttons:
            button.deleteLater()
        self._saved_buttons = []
        while self.saved_row.count():
            item = self.saved_row.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for strokes in self._saved:
            button = SavedSignature(strokes, self)
            button.clicked.connect(lambda _=False, b=button: self._select_saved(b))
            self.saved_row.addWidget(button)
            self._saved_buttons.append(button)
        self.saved_row.addStretch(1)
        self._saved_holder.setVisible(bool(self._saved))
        if self._saved_buttons:
            self._select_saved(self._saved_buttons[0])

    def _select_saved(self, chosen: SavedSignature) -> None:
        for button in self._saved_buttons:
            button.setChecked(button is chosen)
        self._refresh_state()

    def _selected_saved(self) -> SavedSignature | None:
        return next((b for b in self._saved_buttons if b.isChecked()), None)

    def _refresh_state(self) -> None:
        drawing = bool(self.pad.strokes)
        if drawing:
            for button in self._saved_buttons:
                button.setChecked(False)
        selected = self._selected_saved()
        self.use_button.setEnabled(drawing or selected is not None)
        self.use_button.setText(self.tr("Guardar y usar") if drawing else self.tr("Usar firma"))
        self.delete_button.setVisible(selected is not None and not drawing)

    def _delete_selected(self) -> None:
        selected = self._selected_saved()
        if selected is None:
            return
        self._saved = [s for s in self._saved if s is not selected.strokes]
        settings().set_signatures(self._saved)
        self._rebuild_saved()
        self._refresh_state()

    def _use(self) -> None:
        if self.pad.strokes:
            strokes = self.pad.normalized()
            self._saved = [strokes] + self._saved
            settings().set_signatures(self._saved)
            self._chosen = strokes
        else:
            selected = self._selected_saved()
            if selected is None:
                return
            self._chosen = selected.strokes
        self.accept()

    def strokes(self) -> list | None:
        return self._chosen
