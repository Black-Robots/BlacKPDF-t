"""Barra flotante de anotación sobre el visor."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QMenu, QSlider, QToolButton, QVBoxLayout, QWidget

from . import formatting, icons, theme
from .interaction import PRESET_COLORS, STYLED_TOOLS, WIDTH_TOOLS, ToolStyle, load_style
from .stamps import StampChoice, StampPopup
from .widgets import SHADOW_TOP, ColorButton, FloatingPanel, IconButton, separator

TOOL_GROUPS = (
    (("select", "mouse-pointer-2"), ("hand", "hand")),
    (("pen", "pencil"), ("highlight", "highlighter"), ("underline", "underline"), ("strikeout", "strikethrough")),
    (("rect", "square"), ("ellipse", "circle"), ("arrow", "move-up-right"), ("line", "minus"), ("polygon", "pentagon")),
    (("text", "type"), ("note", "sticky-note"), ("stamp", "stamp"), ("signature", "signature")),
    (("redact", "eye-off"), ("eraser", "eraser")),
)


def _tr(text: str) -> str:
    return QCoreApplication.translate("AnnotationTools", text)


def tool_labels() -> dict[str, str]:
    return {
        "select": _tr("Seleccionar texto (V)"),
        "hand": _tr("Mano (H)"),
        "pen": _tr("Lápiz (P)"),
        "highlight": _tr("Resaltar (R)"),
        "underline": _tr("Subrayar (U)"),
        "strikeout": _tr("Tachar (T)"),
        "rect": _tr("Rectángulo"),
        "ellipse": _tr("Elipse"),
        "arrow": _tr("Flecha"),
        "line": _tr("Línea"),
        "polygon": _tr("Polígono: clic en cada vértice; doble clic o Intro para cerrar"),
        "stamp": _tr("Sello: Aprobado, Borrador, Revisado... (S)"),
        "text": _tr("Cuadro de texto (X)"),
        "note": _tr("Nota (N)"),
        "signature": _tr("Firma (F)"),
        "eraser": _tr("Goma: borra la anotación bajo el cursor (E)"),
        "redact": _tr("Censurar: elimina de forma definitiva lo que cubras (C)"),
    }


COMPACT_HIDDEN = ("underline", "strikeout", "ellipse", "line", "arrow", "polygon", "note")

TOOL_SHORTCUTS = {
    "select": "V", "hand": "H", "pen": "P", "highlight": "R", "underline": "U",
    "strikeout": "T", "text": "X", "note": "N", "stamp": "S", "signature": "F", "eraser": "E", "redact": "C",
}


class WidthPopup(QFrame):
    changed = Signal(float)

    def __init__(self, value: float, minimum: float, maximum: float, step: float, suffix: str, title: str, parent=None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("FloatingBar")
        self._step = step
        self._suffix = suffix
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 12)
        layout.setSpacing(6)
        header = QHBoxLayout()
        caption = QLabel(title)
        caption.setProperty("role", "caption")
        header.addWidget(caption)
        header.addStretch(1)
        self.value_label = QLabel()
        header.addWidget(self.value_label)
        layout.addLayout(header)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(minimum / step), int(maximum / step))
        self.slider.setValue(int(round(value / step)))
        self.slider.setFixedWidth(200)
        self.slider.valueChanged.connect(self._changed)
        layout.addWidget(self.slider)
        self._update_label()

    def _changed(self, raw: int) -> None:
        self._update_label()
        self.changed.emit(raw * self._step)

    def _update_label(self) -> None:
        value = self.slider.value() * self._step
        text = formatting.decimal(value)
        self.value_label.setText(f"{text} {self._suffix}")


class AnnotationBar(FloatingPanel):
    toolSelected = Signal(str)
    styleChanged = Signal(str, object)
    stampChanged = Signal(object)

    def __init__(self, parent: QWidget):
        super().__init__(parent, inner=(6, 5, 6, 5))
        self._labels = tool_labels()
        layout = QHBoxLayout(self)
        self.apply_margins(layout)
        layout.setSpacing(2)
        self._buttons: dict[str, IconButton] = {}
        for group_index, group in enumerate(TOOL_GROUPS):
            if group_index:
                layout.addSpacing(4)
                layout.addWidget(separator(vertical=True))
                layout.addSpacing(4)
            for name, icon_name in group:
                button = IconButton(icon_name, self._labels[name], self, checkable=True, size=34, icon_size=18)
                button.clicked.connect(lambda _=False, n=name: self.toolSelected.emit(n))
                layout.addWidget(button)
                self._buttons[name] = button
        self.more_button = IconButton("ellipsis", self.tr("Más herramientas"), self, checkable=True, size=34)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self.more_button)
        for name in COMPACT_HIDDEN:
            icon_name = dict(item for group in TOOL_GROUPS for item in group)[name]
            action = self.more_menu.addAction(icons.icon(icon_name), self._labels[name])
            action.triggered.connect(lambda _=False, n=name: self.toolSelected.emit(n))
        self.more_button.setMenu(self.more_menu)
        self.more_button.setVisible(False)
        layout.addWidget(self.more_button)
        self._compact = False
        layout.addSpacing(4)
        self._style_separator = separator(vertical=True)
        layout.addWidget(self._style_separator)
        layout.addSpacing(4)
        self.color_button = ColorButton("#E5484D", PRESET_COLORS, self)
        self.color_button.colorChanged.connect(self._color_changed)
        layout.addWidget(self.color_button)
        self.width_button = IconButton("sliders-horizontal", self.tr("Grosor"), self, size=34)
        self.width_button.clicked.connect(self._open_width)
        layout.addWidget(self.width_button)
        self.stamp_button = IconButton("chevron-down", self.tr("Elegir sello"), self, size=34, icon_size=14, text=" ")
        # La flechita a la derecha del nombre del sello.
        self.stamp_button.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.stamp_button.clicked.connect(self._open_stamps)
        layout.addWidget(self.stamp_button)
        self._stamp = StampChoice.load()
        self._active = "select"
        self._style: ToolStyle | None = None
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(theme.ANIMATION_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.set_stamp(self._stamp)
        self.set_active("select")

    def set_compact(self, compact: bool) -> None:
        """Con poco espacio, las herramientas menos usadas pasan al menú «Más»."""
        if compact == self._compact:
            return
        self._compact = compact
        for name in COMPACT_HIDDEN:
            self._buttons[name].setVisible(not compact)
        self.more_button.setVisible(compact)
        self.set_active(self._active)

    def full_width(self) -> int:
        """Ancho que ocupa la barra sin compactar."""
        hidden_width = sum(self._buttons[n].sizeHint().width() + 2 for n in COMPACT_HIDDEN)
        width = self.sizeHint().width()
        if self._compact:
            width += hidden_width - self.more_button.sizeHint().width()
        return width

    def set_active(self, name: str) -> None:
        self._active = name
        for key, button in self._buttons.items():
            button.setChecked(key == name)
        in_menu = self._compact and name in COMPACT_HIDDEN
        self.more_button.setChecked(in_menu)
        icon_name = dict(item for group in TOOL_GROUPS for item in group).get(name, "ellipsis") if in_menu else "ellipsis"
        self.more_button.set_icon_name(icon_name)
        styled = name in STYLED_TOOLS
        self.color_button.setVisible(styled)
        self.width_button.setVisible(name in WIDTH_TOOLS or name == "text")
        self.stamp_button.setVisible(name == "stamp")
        self._style_separator.setVisible(styled or name == "stamp")
        if styled:
            self._style = load_style(name)
            self.color_button.set_color(self._style.color)
            self.width_button.setToolTip(self.tr("Tamaño del texto") if name == "text" else self.tr("Grosor"))
        self.adjustSize()

    def set_style(self, style: ToolStyle) -> None:
        self._style = style
        self.color_button.set_color(style.color)

    def set_stamp(self, choice: StampChoice) -> None:
        self._stamp = choice
        self.stamp_button.setText(choice.label)
        self.adjustSize()

    def _open_stamps(self) -> None:
        popup = StampPopup(self._stamp, self)
        popup.picked.connect(self._stamp_picked)
        popup.show_below(self.stamp_button)

    def _stamp_picked(self, choice: StampChoice) -> None:
        self.set_stamp(choice)
        self.stampChanged.emit(choice)

    def _color_changed(self, value: str) -> None:
        if self._style is None:
            return
        self._style.color = value
        self.styleChanged.emit(self._active, self._style)

    def _open_width(self) -> None:
        if self._style is None:
            return
        if self._active == "text":
            popup = WidthPopup(self._style.font_size, 6, 72, 1, "pt", self.tr("Tamaño del texto"), self)
            popup.changed.connect(self._font_size_changed)
        else:
            popup = WidthPopup(self._style.width, 0.5, 16, 0.5, "pt", self.tr("Grosor del trazo"), self)
            popup.changed.connect(self._width_changed)
        popup.adjustSize()
        pos = self.width_button.mapToGlobal(QPoint(0, self.width_button.height() + 8))
        popup.move(pos.x() - popup.width() // 2 + self.width_button.width() // 2, pos.y())
        popup.show()

    def _width_changed(self, value: float) -> None:
        if self._style is not None:
            self._style.width = value
            self.styleChanged.emit(self._active, self._style)

    def _font_size_changed(self, value: float) -> None:
        if self._style is not None:
            self._style.font_size = value
            self.styleChanged.emit(self._active, self._style)

    def place(self, area_width: int, top: int, animate: bool = False) -> None:
        self.adjustSize()
        target = QPoint(max(0, (area_width - self.width()) // 2), top - SHADOW_TOP)
        if animate:
            self._slide.stop()
            self._slide.setStartValue(QPoint(target.x(), top - 16))
            self._slide.setEndValue(target)
            self._slide.start()
        else:
            self.move(target)

    def sizeHint(self) -> QSize:
        return super().sizeHint()
