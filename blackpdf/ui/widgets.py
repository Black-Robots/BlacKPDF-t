"""Widgets reutilizables con la estética de BlackPDF."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QPropertyAnimation,
    QRectF,
    QSize,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core.ranges import is_valid_spec
from . import icons, theme
from .settings import settings

PDF_EXTENSIONS = (".pdf",)


def tr(text: str) -> str:
    from PySide6.QtCore import QCoreApplication

    return QCoreApplication.translate("Widgets", text)


def pdf_filter() -> str:
    return tr("Documentos PDF (*.pdf)")


def open_filter() -> str:
    return tr("Documentos compatibles (*.pdf *.xps *.oxps *.epub *.cbz *.fb2 *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif *.webp)")


def open_any_filter() -> str:
    """Filtro de «Abrir»: documentos y modelos 3D sueltos."""
    return ";;".join((
        tr("Todos los compatibles (*.pdf *.xps *.oxps *.epub *.cbz *.fb2 *.png *.jpg *.jpeg *.tif *.tiff *.bmp *.gif "
           "*.webp *.u3d *.prc)"),
        tr("Documentos PDF (*.pdf)"),
        tr("Modelos 3D (*.u3d *.prc)"),
    ))


def image_filter() -> str:
    return tr("Imágenes (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp *.pnm *.jxr *.psd)")


def set_role(widget: QWidget, role: str) -> QWidget:
    widget.setProperty("role", role)
    return widget


def label(text: str, role: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    if role:
        widget.setProperty("role", role)
    widget.setWordWrap(wrap)
    return widget


def separator(vertical: bool = False) -> QFrame:
    line = QFrame()
    line.setProperty("role", "separator")
    if vertical:
        line.setFixedWidth(1)
        line.setFixedHeight(20)
    else:
        line.setFixedHeight(1)
    return line


SHADOW_SIDE = 14
SHADOW_TOP = 10
SHADOW_BOTTOM = 18


def paint_floating(painter: QPainter, rect: QRectF, radius: float = 12.0) -> None:
    """Panel flotante con sombra suave pintada a mano.

    Se evita QGraphicsEffect a propósito: anidado o con hijos que se repintan
    a menudo (listas, campos de texto) deja zonas sin dibujar.
    """
    p = theme.current()
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    strength = 0.32 if p.is_dark else 0.09
    steps = 7
    for step in range(steps, 0, -1):
        spread = step * 2.0
        shade = QColor(0, 0, 0)
        shade.setAlphaF(strength * (1.0 - step / (steps + 1)) / 2.2)
        painter.setBrush(shade)
        painter.drawRoundedRect(rect.adjusted(-spread, -spread + 4, spread, spread + 5), radius + spread, radius + spread)
    painter.setPen(QPen(p.qcolor("border_strong"), 1))
    painter.setBrush(p.qcolor("surface"))
    painter.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)
    painter.restore()


class FloatingPanel(QFrame):
    """Contenedor flotante con fondo redondeado y sombra, sin efectos gráficos.

    El widget es algo mayor que el panel visible: los márgenes alojan la sombra.
    `inner` son los márgenes del contenido dentro del panel.
    """

    def __init__(self, parent: QWidget | None = None, inner: tuple[int, int, int, int] = (6, 5, 6, 5),
                 radius: float = 12.0):
        super().__init__(parent)
        self._radius = radius
        left, top, right, bottom = inner
        self._layout_margins = (SHADOW_SIDE + left, SHADOW_TOP + top, SHADOW_SIDE + right, SHADOW_BOTTOM + bottom)

    def apply_margins(self, layout) -> None:
        layout.setContentsMargins(*self._layout_margins)

    def panel_rect(self) -> QRectF:
        return QRectF(self.rect()).adjusted(SHADOW_SIDE, SHADOW_TOP, -SHADOW_SIDE, -SHADOW_BOTTOM)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        paint_floating(painter, self.panel_rect(), self._radius)


class AutoStack(QStackedWidget):
    """QStackedWidget que solo ocupa la altura de la página visible."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.currentChanged.connect(self._changed)

    def _changed(self, _index: int) -> None:
        for index in range(self.count()):
            widget = self.widget(index)
            policy = QSizePolicy.Policy.Preferred if widget is self.currentWidget() else QSizePolicy.Policy.Ignored
            widget.setSizePolicy(QSizePolicy.Policy.Preferred, policy)
        self.updateGeometry()
        self.adjustSize()

    def addWidget(self, widget: QWidget) -> int:
        index = super().addWidget(widget)
        self._changed(self.currentIndex())
        return index

    def sizeHint(self) -> QSize:
        current = self.currentWidget()
        return current.sizeHint() if current is not None else super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        current = self.currentWidget()
        return current.minimumSizeHint() if current is not None else super().minimumSizeHint()


class SectionLabel(QLabel):
    def __init__(self, text: str, parent: QWidget | None = None):
        super().__init__(text.upper(), parent)
        self.setProperty("role", "section")
        font = self.font()
        font.setLetterSpacing(font.SpacingType.AbsoluteSpacing, 0.8)
        self.setFont(font)


class IconButton(QToolButton):
    def __init__(
        self,
        icon_name: str,
        tooltip: str = "",
        parent: QWidget | None = None,
        *,
        checkable: bool = False,
        size: int = 32,
        icon_size: int = 18,
        role: str = icons.ROLE_DEFAULT,
        text: str = "",
    ):
        super().__init__(parent)
        self._icon_name = icon_name
        self.setIcon(icons.icon(icon_name, role))
        self.setIconSize(QSize(icon_size, icon_size))
        self.setCheckable(checkable)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRaise(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        if text:
            self.setText(text)
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            self.setProperty("variant", "text")
            self.setFixedHeight(size)
        else:
            self.setFixedSize(size, size)
        if tooltip:
            self.setToolTip(tooltip)

    def set_icon_name(self, name: str, role: str = icons.ROLE_DEFAULT) -> None:
        self._icon_name = name
        self.setIcon(icons.icon(name, role))


class SplitButton(QFrame):
    """Botón con una flechita al lado que despliega más opciones (Abrir y recientes, Guardar y Guardar como)."""

    clicked = Signal()

    def __init__(self, icon_name: str, tooltip: str, menu_tooltip: str, parent: QWidget | None = None, size: int = 32):
        super().__init__(parent)
        self.setObjectName("SplitButton")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.button = IconButton(icon_name, tooltip, self, size=size)
        self.button.setProperty("split", "main")
        self.button.clicked.connect(self.clicked)
        layout.addWidget(self.button)
        self.arrow = IconButton("chevron-down", menu_tooltip, self, size=size, icon_size=12)
        self.arrow.setProperty("split", "arrow")
        self.arrow.setFixedWidth(16)
        self.arrow.clicked.connect(self.show_menu)
        layout.addWidget(self.arrow)
        self.menu = QMenu(self)
        self.menu.setToolTipsVisible(True)

    def show_menu(self) -> None:
        """Despliega el menú alineado con el botón entero, no solo con la flecha."""
        self.arrow.setDown(True)
        self.menu.exec(self.mapToGlobal(QPoint(0, self.height() + 4)))
        self.arrow.setDown(False)


class Button(QPushButton):
    def __init__(self, text: str, variant: str = "", icon_name: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        if variant:
            self.setProperty("variant", variant)
        if icon_name:
            role = icons.ROLE_ON_ACCENT if variant == "primary" else icons.ROLE_DEFAULT
            self.setIcon(icons.icon(icon_name, role))
            self.setIconSize(QSize(16, 16))
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_variant(self, variant: str) -> None:
        self.setProperty("variant", variant)
        self.style().unpolish(self)
        self.style().polish(self)


class SegmentedControl(QFrame):
    """Grupo de opciones excluyentes con aspecto de interruptor."""

    changed = Signal(str)

    def __init__(self, options: Sequence[tuple[str, str]], parent: QWidget | None = None, icons_map: dict | None = None,
                 tooltips: dict | None = None):
        super().__init__(parent)
        self.setProperty("role", "segmented")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        self._buttons: dict[str, QToolButton] = {}
        self._value = ""
        for key, text in options:
            button = QToolButton(self)
            button.setText(text)
            button.setCheckable(True)
            button.setProperty("variant", "segment")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            if icons_map and key in icons_map:
                button.setIcon(icons.icon(icons_map[key]))
                button.setIconSize(QSize(16, 16))
                button.setToolButtonStyle(
                    Qt.ToolButtonStyle.ToolButtonTextBesideIcon if text else Qt.ToolButtonStyle.ToolButtonIconOnly
                )
            if tooltips and key in tooltips:
                button.setToolTip(tooltips[key])
            button.clicked.connect(lambda _=False, k=key: self.set_value(k))
            layout.addWidget(button)
            self._buttons[key] = button
        if options:
            self.set_value(options[0][0], emit=False)

    def value(self) -> str:
        return self._value

    def set_value(self, key: str, emit: bool = True) -> None:
        if key not in self._buttons:
            return
        for k, button in self._buttons.items():
            button.setChecked(k == key)
        if key != self._value:
            self._value = key
            if emit:
                self.changed.emit(key)

    def set_enabled_option(self, key: str, enabled: bool) -> None:
        if key in self._buttons:
            self._buttons[key].setEnabled(enabled)


class ColorSwatch(QToolButton):
    def __init__(self, color: str, parent: QWidget | None = None, size: int = 22):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size, size)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(color.upper())
        self.selected = False

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(3, 3, -3, -3)
        p = theme.current()
        if self.selected or self.underMouse():
            painter.setPen(QPen(p.qcolor("accent") if self.selected else p.qcolor("border_strong"), 2))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        painter.setPen(QPen(p.qcolor("border_strong"), 1))
        painter.setBrush(QColor(self.color))
        painter.drawEllipse(rect)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class ColorPopup(QFrame):
    picked = Signal(str)

    def __init__(self, current: str, presets: Sequence[str], parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("FloatingBar")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)
        grid = QGridLayout()
        grid.setSpacing(4)
        colors = list(presets) + [c for c in settings().custom_colors if c not in presets]
        for index, value in enumerate(colors):
            swatch = ColorSwatch(value, self, 26)
            swatch.selected = value.lower() == current.lower()
            swatch.clicked.connect(lambda _=False, v=value: self._choose(v))
            grid.addWidget(swatch, index // 6, index % 6)
        layout.addLayout(grid)
        custom = Button(tr("Personalizado..."), "ghost", "palette")
        custom.clicked.connect(lambda: self._custom(current))
        layout.addWidget(custom)

    def _choose(self, value: str) -> None:
        self.picked.emit(value)
        self.close()

    def _custom(self, current: str) -> None:
        color = QColorDialog.getColor(QColor(current), self.parentWidget(), tr("Elegir color"))
        if color.isValid():
            value = color.name()
            stored = settings().custom_colors
            if value not in stored:
                settings().custom_colors = ([value] + stored)[:6]
            self._choose(value)
        else:
            self.close()


class ColorButton(QToolButton):
    colorChanged = Signal(str)

    def __init__(self, color: str = "#E5484D", presets: Sequence[str] = (), parent: QWidget | None = None):
        super().__init__(parent)
        self._color = color
        self._presets = presets
        self.setFixedSize(32, 32)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(tr("Color"))
        self.clicked.connect(self._open)

    def color(self) -> str:
        return self._color

    def set_color(self, value: str) -> None:
        self._color = value
        self.update()

    def _open(self) -> None:
        popup = ColorPopup(self._color, self._presets, self)
        popup.picked.connect(self._picked)
        popup.adjustSize()
        pos = self.mapToGlobal(QPoint(0, self.height() + 6))
        popup.move(pos.x() - popup.width() // 2 + self.width() // 2, pos.y())
        popup.show()

    def _picked(self, value: str) -> None:
        self.set_color(value)
        self.colorChanged.emit(value)

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        rect = QRectF(self.rect()).adjusted(8, 8, -8, -8)
        painter.setPen(QPen(p.qcolor("border_strong"), 1.2))
        painter.setBrush(QColor(self._color))
        painter.drawEllipse(rect)


class Toast(FloatingPanel):
    """Aviso breve en la parte inferior de la ventana, con acción opcional."""

    _active: "Toast | None" = None

    def __init__(self, parent: QWidget, text: str, action: str = "", callback=None, kind: str = "info"):
        super().__init__(parent, inner=(14, 8, 8 if action else 14, 8))
        layout = QHBoxLayout(self)
        self.apply_margins(layout)
        layout.setSpacing(10)
        icon_name = {"success": "circle-check", "error": "circle-alert", "warning": "triangle-alert"}.get(kind, "info")
        role = {"success": icons.ROLE_SUCCESS, "error": icons.ROLE_DANGER, "warning": icons.ROLE_WARNING}.get(kind, icons.ROLE_MUTED)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(icon_name, 16, role, self.devicePixelRatioF()))
        layout.addWidget(glyph)
        message = QLabel(text)
        layout.addWidget(message)
        if action:
            button = Button(action, "ghost")
            button.setStyleSheet(f"color: {theme.current().accent}; padding: 4px 10px;")
            button.clicked.connect(lambda: (callback() if callback else None, self.dismiss()))
            layout.addWidget(button)
        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.setDuration(theme.ANIMATION_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.dismiss)
        parent.installEventFilter(self)

    @classmethod
    def show_message(cls, parent: QWidget, text: str, action: str = "", callback=None,
                     kind: str = "info", duration: int = 3500) -> "Toast":
        if cls._active is not None:
            try:
                cls._active.hide()
                cls._active.deleteLater()
            except RuntimeError:
                pass
        toast = cls(parent, text, action, callback, kind)
        cls._active = toast
        toast.adjustSize()
        target = toast._target()
        toast.move(target.x(), target.y() + 16)
        toast.show()
        toast.raise_()
        toast._anim.setStartValue(toast.pos())
        toast._anim.setEndValue(target)
        toast._anim.start()
        toast._timer.start(duration if not action else max(duration, 6000))
        return toast

    def _target(self) -> QPoint:
        parent = self.parentWidget()
        if parent is None:
            return self.pos()
        x = (parent.width() - self.width()) // 2
        y = parent.height() - self.height() - 16
        return QPoint(max(0, x), max(0, y))

    def _reposition(self) -> None:
        self.adjustSize()
        self.move(self._target())

    def eventFilter(self, watched, event) -> bool:
        if event.type() == QEvent.Type.Resize:
            self._reposition()
        return False

    def dismiss(self) -> None:
        if Toast._active is self:
            Toast._active = None
        if getattr(self, "_closing", False):
            return
        self._closing = True
        self._timer.stop()
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(self.pos() + QPoint(0, 24))
        self._anim.finished.connect(self.deleteLater)
        self._anim.start()


def dropped_paths(event, extensions: Sequence[str] | None = None) -> list[str]:
    paths = []
    if event.mimeData().hasUrls():
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = url.toLocalFile()
                if os.path.isfile(path) and (extensions is None or Path(path).suffix.lower() in extensions):
                    paths.append(path)
    return paths


class DropZone(QFrame):
    """Zona para soltar archivos, con borde discontinuo que se ilumina."""

    filesDropped = Signal(list)
    clicked = Signal()

    def __init__(self, extensions: Sequence[str] | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.extensions = tuple(extensions) if extensions else None
        self.setAcceptDrops(True)
        self._active = False
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, theme.RADIUS * 1.5, theme.RADIUS * 1.5)
        fill = p.qcolor("accent_soft") if self._active else (p.qcolor("hover") if self._hover else p.qcolor("surface"))
        painter.fillPath(path, fill)
        pen = QPen(p.qcolor("accent") if self._active or self._hover else p.qcolor("border_strong"), 1.5)
        pen.setDashPattern([6, 5])
        painter.setPen(pen)
        painter.drawPath(path)

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if dropped_paths(event, self.extensions):
            self._active = True
            self.update()
            event.acceptProposedAction()

    def dragLeaveEvent(self, event) -> None:
        self._active = False
        self.update()

    def dropEvent(self, event: QDropEvent) -> None:
        self._active = False
        self.update()
        paths = dropped_paths(event, self.extensions)
        if paths:
            event.acceptProposedAction()
            self.filesDropped.emit(paths)


class FileListWidget(QWidget):
    """Lista reordenable de archivos (arrastrar, subir, bajar, quitar)."""

    changed = Signal()

    def __init__(self, extensions: Sequence[str], file_filter: str, parent: QWidget | None = None,
                 describe=None):
        super().__init__(parent)
        self.extensions = tuple(extensions)
        self.file_filter = file_filter
        self.describe = describe
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.list = QListWidget(self)
        self.list.setProperty("role", "filelist")
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setIconSize(QSize(18, 18))
        self.list.setMinimumHeight(180)
        self.list.setAcceptDrops(True)
        self.list.model().rowsMoved.connect(lambda *_: self.changed.emit())
        self.list.viewport().installEventFilter(self)
        self.list.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        layout.addWidget(self.list, 1)

        self.empty_hint = label(tr("Arrastra archivos aquí o pulsa Añadir"), "muted")
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint.setParent(self.list.viewport())
        self.empty_hint.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        bar = QHBoxLayout()
        bar.setSpacing(4)
        self.add_button = Button(tr("Añadir"), "", "plus")
        self.add_button.clicked.connect(self._browse)
        bar.addWidget(self.add_button)
        bar.addStretch(1)
        self.up_button = IconButton("arrow-up", tr("Subir"))
        self.down_button = IconButton("arrow-down", tr("Bajar"))
        self.sort_button = IconButton("arrow-down-up", tr("Ordenar por nombre"))
        self.remove_button = IconButton("trash-2", tr("Quitar"))
        self.up_button.clicked.connect(lambda: self._move(-1))
        self.down_button.clicked.connect(lambda: self._move(1))
        self.sort_button.clicked.connect(self._sort)
        self.remove_button.clicked.connect(self._remove)
        for button in (self.up_button, self.down_button, self.sort_button, self.remove_button):
            bar.addWidget(button)
        layout.addLayout(bar)
        self._refresh_hint()

    def eventFilter(self, watched, event) -> bool:
        kind = event.type()
        if kind in (QEvent.Type.DragEnter, QEvent.Type.DragMove):
            if dropped_paths(event, self.extensions):
                event.acceptProposedAction()
                return True
        elif kind == QEvent.Type.Drop:
            paths = dropped_paths(event, self.extensions)
            if paths:
                self.add_files(paths)
                event.acceptProposedAction()
                return True
        elif kind == QEvent.Type.Resize:
            self.empty_hint.setGeometry(self.list.viewport().rect())
        return False

    def _browse(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, tr("Añadir archivos"), settings().last_directory, self.file_filter)
        if paths:
            settings().last_directory = paths[0]
            self.add_files(paths)

    def add_files(self, paths: Sequence[str]) -> None:
        for path in paths:
            if Path(path).suffix.lower() not in self.extensions:
                continue
            item = QListWidgetItem(icons.icon("file-text" if Path(path).suffix.lower() == ".pdf" else "image"), "")
            name = Path(path).name
            detail = self.describe(path) if self.describe else ""
            item.setText(f"{name}    {detail}" if detail else name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self.list.addItem(item)
        self._refresh_hint()
        self.changed.emit()

    def files(self) -> list[str]:
        return [self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]

    def _move(self, delta: int) -> None:
        rows = sorted({self.list.row(i) for i in self.list.selectedItems()})
        if not rows:
            return
        if delta < 0 and rows[0] == 0 or delta > 0 and rows[-1] == self.list.count() - 1:
            return
        order = rows if delta < 0 else list(reversed(rows))
        for row in order:
            item = self.list.takeItem(row)
            self.list.insertItem(row + delta, item)
            item.setSelected(True)
        self.changed.emit()

    def _sort(self) -> None:
        self.list.sortItems()
        self.changed.emit()

    def _remove(self) -> None:
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self._refresh_hint()
        self.changed.emit()

    def _refresh_hint(self) -> None:
        self.empty_hint.setVisible(self.list.count() == 0)
        self.empty_hint.setGeometry(self.list.viewport().rect())


class PathPicker(QWidget):
    """Campo de ruta con botón Examinar: abrir archivo, guardar archivo o carpeta."""

    changed = Signal(str)

    def __init__(self, mode: str, file_filter: str = "", parent: QWidget | None = None, placeholder: str = ""):
        super().__init__(parent)
        self.mode = mode
        self.file_filter = file_filter
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.edit = QLineEdit(self)
        self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self.changed)
        layout.addWidget(self.edit, 1)
        self.button = Button(tr("Examinar..."), "", "folder-open")
        self.button.clicked.connect(self._browse)
        layout.addWidget(self.button)

    def path(self) -> str:
        return self.edit.text().strip()

    def set_path(self, value: str) -> None:
        self.edit.setText(value)

    def _browse(self) -> None:
        start = self.path() or settings().last_directory
        if self.mode == "folder":
            path = QFileDialog.getExistingDirectory(self, tr("Elegir carpeta"), start)
        elif self.mode == "save":
            path, _ = QFileDialog.getSaveFileName(self, tr("Guardar como"), start, self.file_filter)
            if path and self.file_filter == pdf_filter() and not path.lower().endswith(".pdf"):
                path += ".pdf"
        else:
            path, _ = QFileDialog.getOpenFileName(self, tr("Abrir archivo"), start, self.file_filter)
        if path:
            settings().last_directory = path
            self.set_path(path)


class PageRangeEdit(QLineEdit):
    """Campo de rangos (1-3, 5, 8-10) con validación en vivo."""

    validityChanged = Signal(bool)

    def __init__(self, parent: QWidget | None = None, allow_empty: bool = True):
        super().__init__(parent)
        self.allow_empty = allow_empty
        self.page_count = 0
        self._valid = True
        self.setPlaceholderText(tr("Todas las páginas") if allow_empty else tr("Ejemplo: 1-3, 5, 8-10"))
        self.textChanged.connect(self._validate)

    def set_page_count(self, count: int) -> None:
        self.page_count = count
        self._validate()

    def is_valid(self) -> bool:
        return self._valid

    def _validate(self) -> None:
        text = self.text().strip()
        if not text:
            valid = self.allow_empty
        elif self.page_count <= 0:
            valid = True
        else:
            valid = is_valid_spec(text, self.page_count)
        if valid != self._valid:
            self._valid = valid
            self.validityChanged.emit(valid)
        self.setProperty("invalid", "true" if (text and not valid) else "false")
        self.style().unpolish(self)
        self.style().polish(self)


class SearchPill(QPushButton):
    """Botón con aspecto de campo de búsqueda que abre la paleta de comandos."""

    def sizeHint(self) -> QSize:
        return QSize(330, 34)

    def __init__(self, text: str, shortcut: str, parent: QWidget | None = None):
        super().__init__(parent)
        self._text = text
        self._shortcut = shortcut
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(34)
        self.setMinimumWidth(180)
        self.setMaximumWidth(330)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setStyleSheet("QPushButton { background: transparent; border: none; padding: 0; }")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        hovered = self.underMouse()
        painter.setPen(QPen(p.qcolor("text_faint") if hovered else p.qcolor("border_strong"), 1))
        painter.setBrush(p.qcolor("hover") if hovered else p.qcolor("surface_alt"))
        painter.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)
        glyph = icons.pixmap("search", 16, icons.ROLE_MUTED, self.devicePixelRatioF())
        painter.drawPixmap(12, (self.height() - 16) // 2, glyph)
        base_font = painter.font()
        small = painter.font()
        small.setPixelSize(11)
        painter.setFont(small)
        width = painter.fontMetrics().horizontalAdvance(self._shortcut) + 12
        chip = QRectF(self.width() - width - 8, (self.height() - 20) / 2, width, 20)
        painter.setFont(base_font)
        painter.setPen(p.qcolor("text_muted"))
        text_rect = QRectF(36, 0, chip.left() - 44, self.height())
        text = painter.fontMetrics().elidedText(self._text, Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
        painter.setFont(small)
        painter.setPen(QPen(p.qcolor("border_strong"), 1))
        painter.setBrush(p.qcolor("surface"))
        painter.drawRoundedRect(chip, 4, 4)
        painter.setPen(p.qcolor("text_muted"))
        painter.drawText(chip, Qt.AlignmentFlag.AlignCenter, self._shortcut)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class TabCloseButton(QToolButton):
    """Botón de cerrar pestaña: muestra un punto si hay cambios sin guardar y una X al pasar el ratón."""

    # Qt coloca el botón pegado al borde derecho de la pestaña: el margen derecho
    # interno deja la X a la misma distancia del borde que el icono a la izquierda.
    GLYPH = 20
    RIGHT_MARGIN = 8

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.modified = False
        self.setFixedSize(self.GLYPH + self.RIGHT_MARGIN, self.GLYPH)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAutoRaise(True)
        self.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0; }")

    def set_modified(self, modified: bool) -> None:
        self.modified = modified
        self.update()

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        rect = QRectF(0, 0, self.GLYPH, self.GLYPH)
        hovered = self.underMouse()
        if hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(p.qcolor("pressed"))
            painter.drawRoundedRect(rect, 5, 5)
        if self.modified and not hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(p.qcolor("text_secondary"))
            painter.drawEllipse(rect.center(), 4, 4)
            return
        glyph = icons.pixmap("x", 14, icons.ROLE_TEXT if hovered else icons.ROLE_MUTED, self.devicePixelRatioF())
        painter.drawPixmap(3, 3, glyph)

    def hitButton(self, pos) -> bool:
        return QRectF(0, 0, self.GLYPH, self.GLYPH).contains(pos.toPointF() if hasattr(pos, "toPointF") else pos)


FORMAT_ICONS = {
    ".pdf": "file-text",
    ".png": "file-image", ".jpg": "file-image", ".jpeg": "file-image", ".bmp": "file-image",
    ".gif": "file-image", ".tif": "file-image", ".tiff": "file-image", ".webp": "file-image",
    ".pnm": "file-image", ".pgm": "file-image", ".ppm": "file-image", ".pbm": "file-image",
    ".jxr": "file-image", ".psd": "file-image",
    ".epub": "book-open", ".fb2": "book-open", ".mobi": "book-open",
    ".xps": "file-type", ".oxps": "file-type",
    ".cbz": "images",
}


def format_icon(suffix: str):
    """Icono de pestaña según el formato: el PDF en rojo, el resto en neutro."""
    name = FORMAT_ICONS.get(suffix.lower(), "file-text")
    return icons.icon(name, icons.ROLE_ACCENT if suffix.lower() == ".pdf" else icons.ROLE_TEXT)
