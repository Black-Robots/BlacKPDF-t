"""Barra flotante de anotación sobre el visor."""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QSize, Qt, Signal
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QSlider, QVBoxLayout, QWidget

from . import theme
from .interaction import PRESET_COLORS, STYLED_TOOLS, WIDTH_TOOLS, ToolStyle, load_style
from .widgets import ColorButton, IconButton, add_shadow, separator

TOOL_GROUPS = (
    (("select", "mouse-pointer-2"), ("hand", "hand")),
    (("pen", "pencil"), ("highlight", "highlighter"), ("underline", "underline"), ("strikeout", "strikethrough")),
    (("rect", "square"), ("ellipse", "circle"), ("arrow", "move-up-right"), ("line", "minus")),
    (("text", "type"), ("note", "sticky-note"), ("signature", "signature")),
    (("eraser", "eraser"),),
)


def tool_labels(tr) -> dict[str, str]:
    return {
        "select": tr("Seleccionar texto (V)"),
        "hand": tr("Mano (H)"),
        "pen": tr("Lápiz (P)"),
        "highlight": tr("Resaltar (R)"),
        "underline": tr("Subrayar (U)"),
        "strikeout": tr("Tachar (T)"),
        "rect": tr("Rectángulo"),
        "ellipse": tr("Elipse"),
        "arrow": tr("Flecha"),
        "line": tr("Línea"),
        "text": tr("Cuadro de texto (X)"),
        "note": tr("Nota (N)"),
        "signature": tr("Firma (F)"),
        "eraser": tr("Goma: borra la anotación bajo el cursor (E)"),
    }


TOOL_SHORTCUTS = {
    "select": "V", "hand": "H", "pen": "P", "highlight": "R", "underline": "U",
    "strikeout": "T", "text": "X", "note": "N", "signature": "F", "eraser": "E",
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
        text = f"{value:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        self.value_label.setText(f"{text} {self._suffix}")


class AnnotationBar(QFrame):
    toolSelected = Signal(str)
    styleChanged = Signal(str, object)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setObjectName("FloatingBar")
        self._labels = tool_labels(self.tr)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 5, 6, 5)
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
        add_shadow(self, 30, 8)
        self._active = "select"
        self._style: ToolStyle | None = None
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._slide.setDuration(theme.ANIMATION_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.set_active("select")

    def set_active(self, name: str) -> None:
        self._active = name
        for key, button in self._buttons.items():
            button.setChecked(key == name)
        styled = name in STYLED_TOOLS
        self.color_button.setVisible(styled)
        self.width_button.setVisible(name in WIDTH_TOOLS or name == "text")
        self._style_separator.setVisible(styled)
        if styled:
            self._style = load_style(name)
            self.color_button.set_color(self._style.color)
            self.width_button.setToolTip(self.tr("Tamaño del texto") if name == "text" else self.tr("Grosor"))
        self.adjustSize()

    def set_style(self, style: ToolStyle) -> None:
        self._style = style
        self.color_button.set_color(style.color)

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
        target = QPoint(max(8, (area_width - self.width()) // 2), top)
        if animate:
            self._slide.stop()
            self._slide.setStartValue(QPoint(target.x(), top - 16))
            self._slide.setEndValue(target)
            self._slide.start()
        else:
            self.move(target)

    def sizeHint(self) -> QSize:
        return super().sizeHint()
