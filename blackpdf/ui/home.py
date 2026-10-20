"""Pantalla de inicio: zona para soltar archivos, recientes y herramientas."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QRect, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core.document import OPENABLE_EXTENSIONS
from ..core.model3d import MODEL_EXTENSIONS
from . import formatting, icons, theme
from .settings import settings
from .tools import ToolSpec, categories, tool_specs
from .widgets import Button, DropZone, SectionLabel, label

CONTENT_MAX_WIDTH = 1180


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(
        round(a.red() + (b.red() - a.red()) * t),
        round(a.green() + (b.green() - a.green()) * t),
        round(a.blue() + (b.blue() - a.blue()) * t),
        round(a.alpha() + (b.alpha() - a.alpha()) * t),
    )


class HoverCard(QWidget):
    """Tarjeta clicable con transición suave de color al pasar el ratón."""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self._t = 0.0
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._set_t)

    def _set_t(self, value) -> None:
        self._t = float(value)
        self.update()

    def _animate(self, target: float) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._t)
        self._anim.setEndValue(target)
        self._anim.start()

    def enterEvent(self, event) -> None:
        self._animate(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._animate(0.0)
        super().leaveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def paint_background(self, painter: QPainter) -> None:
        p = theme.current()
        rect = self.rect().adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, theme.RADIUS + 2, theme.RADIUS + 2)
        fill = _mix(p.qcolor("surface"), p.qcolor("hover"), self._t * 0.6)
        border = _mix(p.qcolor("border"), p.qcolor("accent"), self._t * 0.7)
        painter.fillPath(path, fill)
        painter.setPen(QPen(border, 1))
        painter.drawPath(path)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_background(painter)


class ToolCard(HoverCard):
    activated = Signal(str)

    def __init__(self, spec: ToolSpec, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.setMinimumHeight(132)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 16)
        layout.setSpacing(10)
        self.tile = QLabel(self)
        self.tile.setFixedSize(40, 40)
        layout.addWidget(self.tile)
        title = label(spec.title, "cardtitle", wrap=True)
        layout.addWidget(title)
        description = label(spec.description, "caption", wrap=True)
        description.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
        layout.addWidget(description)
        layout.addStretch(1)
        self.clicked.connect(lambda: self.activated.emit(spec.id))
        self.setToolTip(spec.description)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint_background(painter)
        p = theme.current()
        tile = self.tile.geometry()
        soft = p.qcolor("accent_soft")
        strong = p.qcolor("accent")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(_mix(soft, strong, self._t))
        painter.drawRoundedRect(tile, theme.RADIUS + 2, theme.RADIUS + 2)
        role = icons.ROLE_ON_ACCENT if self._t > 0.5 else icons.ROLE_ACCENT
        glyph = icons.pixmap(self.spec.icon, 20, role, self.devicePixelRatioF())
        painter.drawPixmap(tile.x() + 10, tile.y() + 10, glyph)


class RecentCard(HoverCard):
    activated = Signal(str)
    removeRequested = Signal(str)

    def __init__(self, path: str, parent=None):
        super().__init__(parent)
        self.path = path
        self.setFixedHeight(64)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 10, 10, 10)
        layout.setSpacing(12)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("file-text", 22, icons.ROLE_ACCENT, self.devicePixelRatioF()))
        layout.addWidget(glyph)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        name = label(Path(path).name, "cardtitle")
        name.setMinimumWidth(10)
        texts.addWidget(name)
        detail = Path(path).parent.name
        try:
            modified = dt.datetime.fromtimestamp(Path(path).stat().st_mtime)
            detail = f"{detail}  ·  {formatting.date(modified)}"
        except OSError:
            pass
        caption = label(detail, "caption")
        caption.setMinimumWidth(10)
        texts.addWidget(caption)
        layout.addLayout(texts, 1)
        self.setToolTip(path)
        self.clicked.connect(lambda: self.activated.emit(path))


class CardGrid(QWidget):
    """Rejilla adaptable: tantas columnas como quepan con un ancho mínimo."""

    def __init__(self, min_width: int = 240, gap: int = 12, row_height: int | None = None, parent=None):
        super().__init__(parent)
        self.min_width = min_width
        self.gap = gap
        self.row_height = row_height
        self.cards: list[QWidget] = []
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

    def add(self, card: QWidget) -> None:
        card.setParent(self)
        self.cards.append(card)
        card.show()
        self._relayout()

    def clear(self) -> None:
        for card in self.cards:
            card.hide()
            card.deleteLater()
        self.cards = []
        self._relayout()

    def _columns(self, width: int) -> int:
        return max(1, (width + self.gap) // (self.min_width + self.gap))

    def _row_height(self, column_width: int) -> int:
        if self.row_height:
            return self.row_height
        return max((c.heightForWidth(column_width) if c.hasHeightForWidth() else c.sizeHint().height())
                   for c in self.cards) if self.cards else 0

    def _relayout(self) -> None:
        width = self.width()
        columns = self._columns(width)
        column_width = (width - self.gap * (columns - 1)) / columns if columns else width
        row_height = max(self._row_height(int(column_width)), max((c.minimumHeight() for c in self.cards), default=0))
        for index, card in enumerate(self.cards):
            row, col = divmod(index, columns)
            x = round(col * (column_width + self.gap))
            y = row * (row_height + self.gap)
            card.setGeometry(QRect(x, y, round(column_width), row_height))
        rows = (len(self.cards) + columns - 1) // columns
        height = rows * row_height + max(0, rows - 1) * self.gap
        self.setFixedHeight(max(0, height))

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()


class HomePage(QScrollArea):
    openRequested = Signal()
    filesDropped = Signal(list)
    recentActivated = Signal(str)
    toolActivated = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("HomeScroll")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer = QWidget()
        outer.setObjectName("HomeContent")
        outer_layout = QHBoxLayout(outer)
        outer_layout.setContentsMargins(32, 36, 32, 40)
        self.content = QWidget()
        self.content.setMaximumWidth(CONTENT_MAX_WIDTH)
        outer_layout.addWidget(self.content)
        layout = QVBoxLayout(self.content)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        hero = QHBoxLayout()
        hero.setSpacing(32)
        intro = QVBoxLayout()
        intro.setSpacing(12)
        intro.addStretch(1)
        intro.addWidget(label(self.tr("Todo lo que necesitas para tus PDF"), "hero", wrap=True))
        intro.addWidget(label(self.tr(
            "Visor y editor completo. Libre, gratuito y sin conexión: tus archivos nunca salen de tu equipo."),
            "subtitle", wrap=True))
        intro.addStretch(1)
        hero.addLayout(intro, 5)

        self.drop = DropZone(OPENABLE_EXTENSIONS + MODEL_EXTENSIONS, self)
        self.drop.setMinimumHeight(200)
        drop_layout = QVBoxLayout(self.drop)
        drop_layout.setContentsMargins(24, 24, 24, 24)
        drop_layout.setSpacing(10)
        drop_layout.addStretch(1)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("file-up", 36, icons.ROLE_ACCENT, self.devicePixelRatioF()))
        glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(glyph)
        drop_title = label(self.tr("Arrastra aquí tus archivos"), "cardtitle")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(drop_title)
        open_button = Button(self.tr("Abrir archivo"), "primary", "folder-open")
        open_button.setProperty("variant", "primary")
        open_button.clicked.connect(self.openRequested)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(open_button)
        row.addStretch(1)
        drop_layout.addLayout(row)
        hint = label(self.tr("PDF, imágenes, XPS o EPUB  ·  Ctrl+O"), "caption")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_layout.addWidget(hint)
        drop_layout.addStretch(1)
        self.drop.filesDropped.connect(self.filesDropped)
        self.drop.clicked.connect(self.openRequested)
        hero.addWidget(self.drop, 4)
        layout.addLayout(hero)
        layout.addSpacing(16)

        self.recent_header = QHBoxLayout()
        self.recent_title = SectionLabel(self.tr("Recientes"))
        self.recent_header.addWidget(self.recent_title)
        self.recent_header.addStretch(1)
        self.clear_recents = Button(self.tr("Borrar lista"), "link")
        self.clear_recents.clicked.connect(self._clear_recents)
        self.recent_header.addWidget(self.clear_recents)
        layout.addLayout(self.recent_header)
        self.recent_grid = CardGrid(260, 10, 64)
        layout.addWidget(self.recent_grid)
        layout.addSpacing(12)

        for key, name in categories():
            layout.addWidget(SectionLabel(name))
            grid = CardGrid(176, 12)
            for item in tool_specs():
                if item.category == key:
                    card = ToolCard(item)
                    card.activated.connect(self.toolActivated)
                    grid.add(card)
            layout.addWidget(grid)
            layout.addSpacing(10)

        footer = label(self.tr("BlackPDF {0}  ·  Software libre con licencia AGPL-3.0  ·  Funciona sin conexión").format(__version__), "caption")
        footer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addSpacing(12)
        layout.addWidget(footer)
        layout.addStretch(1)
        self.setWidget(outer)
        self.refresh_recents()

    def refresh_recents(self) -> None:
        self.recent_grid.clear()
        recents = [p for p in settings().recent_files() if Path(p).exists()][:6]
        for path in recents:
            card = RecentCard(path)
            card.activated.connect(self.recentActivated)
            self.recent_grid.add(card)
        visible = bool(recents)
        self.recent_title.setVisible(visible)
        self.clear_recents.setVisible(visible)
        self.recent_grid.setVisible(visible)

    def _clear_recents(self) -> None:
        settings().clear_recents()
        self.refresh_recents()

    def sizeHint(self) -> QSize:
        return QSize(1100, 760)
