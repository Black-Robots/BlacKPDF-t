"""Paleta de comandos (Ctrl+K): busca y ejecuta cualquier acción o herramienta."""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QFont, QFontMetrics, QKeyEvent, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from . import icons, theme
from .widgets import SHADOW_BOTTOM, SHADOW_SIDE, SHADOW_TOP, FloatingPanel


@dataclass
class Command:
    title: str
    category: str
    icon: str
    run: Callable[[], None]
    shortcut: str = ""
    keywords: str = ""


def _fold(text: str) -> str:
    normalized = unicodedata.normalize("NFD", text.lower())
    return "".join(c for c in normalized if unicodedata.category(c) != "Mn")


def score(query: str, command: Command) -> float:
    """Relevancia de un comando para el texto escrito (0 = no se muestra).

    Orden: el título empieza por el texto, lo contiene, alguna palabra del
    título empieza por él, todas las palabras escritas son inicio de palabras
    del título o de las palabras clave, y por último letras en orden dentro
    del título (para erratas y abreviaturas como «cmprm»).
    """
    q = _fold(query.strip())
    if not q:
        return 1.0
    title = _fold(command.title)
    title_words = title.split()
    extra_words = _fold(f"{command.keywords} {command.category}").split()
    if title.startswith(q):
        return 100.0 - len(title) * 0.01
    if q in title:
        return 85.0 - title.index(q) * 0.1
    if any(word.startswith(q) for word in title_words):
        return 75.0
    parts = q.split()
    if all(any(word.startswith(part) for word in title_words + extra_words) for part in parts):
        return 60.0
    if len(q) < 3:
        return 0.0
    position, gaps = 0, 0
    for char in q:
        found = title.find(char, position)
        if found < 0:
            return 0.0
        gaps += found - position
        position = found + 1
    if gaps > 2 * len(q):
        return 0.0
    return max(1.0, 30.0 - gaps)


class CommandDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index) -> QSize:
        return QSize(option.rect.width(), 40)

    def paint(self, painter: QPainter, option, index: QModelIndex) -> None:
        command: Command = index.data(Qt.ItemDataRole.UserRole)
        if command is None:
            return
        p = theme.current()
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(4, 1, -4, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        if selected:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(p.qcolor("hover"))
            painter.drawRoundedRect(rect, theme.RADIUS_SMALL, theme.RADIUS_SMALL)
        glyph = icons.pixmap(command.icon, 18, icons.ROLE_ACCENT if selected else icons.ROLE_MUTED, painter.device().devicePixelRatioF())
        painter.drawPixmap(rect.left() + 12, rect.center().y() - 9, glyph)
        font = QFont(option.font)
        font.setPixelSize(13)
        painter.setFont(font)
        painter.setPen(p.qcolor("text"))
        title_rect = QRect(rect.left() + 42, rect.top(), rect.width() - 220, rect.height())
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(font).elidedText(command.title, Qt.TextElideMode.ElideRight, title_rect.width()))
        small = QFont(option.font)
        small.setPixelSize(11)
        painter.setFont(small)
        metrics = QFontMetrics(small)
        right = rect.right() - 12
        if command.shortcut:
            width = metrics.horizontalAdvance(command.shortcut) + 12
            chip = QRect(right - width, rect.center().y() - 10, width, 20)
            painter.setPen(p.qcolor("border_strong"))
            painter.setBrush(p.qcolor("surface_alt"))
            painter.drawRoundedRect(chip, 4, 4)
            painter.setPen(p.qcolor("text_muted"))
            painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, command.shortcut)
        right = rect.right() - 12 - 116
        painter.setPen(p.qcolor("text_faint"))
        category_rect = QRect(right - 140, rect.top(), 140, rect.height())
        painter.drawText(category_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, command.category)
        painter.restore()


class CommandPalette(QWidget):
    """Capa sobre la ventana con un cuadro de búsqueda y la lista de comandos."""

    closed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self.commands: list[Command] = []
        self.panel = FloatingPanel(self, inner=(8, 8, 8, 8), radius=14)
        layout = QVBoxLayout(self.panel)
        self.panel.apply_margins(layout)
        layout.setSpacing(6)
        search_row = QHBoxLayout()
        search_row.setContentsMargins(10, 4, 6, 4)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("search", 18, icons.ROLE_MUTED, self.devicePixelRatioF()))
        search_row.addWidget(glyph)
        self.edit = QLineEdit()
        self.edit.setProperty("variant", "bare")
        self.edit.setPlaceholderText(self.tr("Escribe una acción o herramienta..."))
        font = self.edit.font()
        font.setPixelSize(15)
        self.edit.setFont(font)
        self.edit.setStyleSheet("font-size: 15px;")
        self.edit.textChanged.connect(self._filter)
        self.edit.installEventFilter(self)
        search_row.addWidget(self.edit, 1)
        hint = QLabel("Esc")
        hint.setProperty("role", "kbd")
        search_row.addWidget(hint)
        layout.addLayout(search_row)
        line = QFrame()
        line.setProperty("role", "separator")
        line.setFixedHeight(1)
        layout.addWidget(line)
        self.list = QListWidget()
        self.list.setItemDelegate(CommandDelegate(self.list))
        self.list.setUniformItemSizes(True)
        self.list.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.itemActivated.connect(self._run_item)
        self.list.itemClicked.connect(self._run_item)
        self.list.setMouseTracking(True)
        layout.addWidget(self.list, 1)
        self.empty = QLabel(self.tr("No hay coincidencias"))
        self.empty.setProperty("role", "muted")
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)
        # Fundido del fondo pintado a mano (sin QGraphicsEffect).
        self._scrim = 1.0
        self._fade = QVariantAnimation(self)
        self._fade.setDuration(theme.ANIMATION_MS)
        self._fade.setStartValue(0.0)
        self._fade.setEndValue(1.0)
        self._fade.valueChanged.connect(self._set_scrim)
        self.hide()
        parent.installEventFilter(self)

    def open(self, commands: list[Command]) -> None:
        self.commands = commands
        self.setGeometry(self.parentWidget().rect())
        self._place_panel()
        self.edit.clear()
        self._filter("")
        self.show()
        self.raise_()
        self._fade.stop()
        self._fade.start()
        self.edit.setFocus()

    def _set_scrim(self, value) -> None:
        self._scrim = float(value)
        self.update()

    def close_palette(self) -> None:
        self.hide()
        self.closed.emit()

    ROW_HEIGHT = 40
    MAX_ROWS = 9

    def _place_panel(self) -> None:
        width = min(640 + 2 * SHADOW_SIDE, self.width() - 24)
        rows = min(max(self.list.count(), 1), self.MAX_ROWS)
        content = 52 + (rows * self.ROW_HEIGHT + 8 if self.list.count() else 56)
        height = min(content + 16 + SHADOW_TOP + SHADOW_BOTTOM, self.height() - 100)
        self.panel.setGeometry((self.width() - width) // 2, max(36, self.height() // 7), width, height)

    def _filter(self, text: str) -> None:
        self.list.clear()
        ranked = sorted(((score(text, c), i, c) for i, c in enumerate(self.commands)), key=lambda t: (-t[0], t[1]))
        for value, _, command in ranked:
            if value <= 0:
                continue
            item = QListWidgetItem(command.title)
            item.setData(Qt.ItemDataRole.UserRole, command)
            self.list.addItem(item)
        if self.list.count():
            self.list.setCurrentRow(0)
        self.empty.setVisible(self.list.count() == 0)
        self.list.setVisible(self.list.count() > 0)
        if self.isVisible() or self.parentWidget() is not None:
            self._place_panel()

    def _run_item(self, item: QListWidgetItem) -> None:
        command: Command = item.data(Qt.ItemDataRole.UserRole)
        self.close_palette()
        if command is not None:
            command.run()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize and self.isVisible():
            self.setGeometry(self.parentWidget().rect())
            self._place_panel()
        if watched is self.edit and isinstance(event, QKeyEvent) and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.close_palette()
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up, Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                row = self.list.currentRow()
                step = {Qt.Key.Key_Down: 1, Qt.Key.Key_Up: -1, Qt.Key.Key_PageDown: 8, Qt.Key.Key_PageUp: -8}[key]
                if self.list.count():
                    self.list.setCurrentRow(max(0, min(self.list.count() - 1, row + step)))
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                item = self.list.currentItem()
                if item is not None:
                    self._run_item(item)
                return True
        return super().eventFilter(watched, event)

    def mousePressEvent(self, event) -> None:
        visible = self.panel.panel_rect().translated(self.panel.pos().toPointF())
        if not visible.contains(event.position()):
            self.close_palette()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        scrim = theme.color("scrim")
        scrim.setAlphaF(scrim.alphaF() * self._scrim)
        painter.fillRect(self.rect(), scrim)
