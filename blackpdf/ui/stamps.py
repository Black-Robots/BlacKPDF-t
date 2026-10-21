"""Sellos: los habituales («Aprobado», «Borrador»...) y los que escribe cada uno."""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pymupdf
from PySide6.QtCore import QCoreApplication, QDateTime, QLocale, QPoint, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import annotations as core_annots
from . import theme
from .settings import settings
from .widgets import Button, ColorSwatch, SectionLabel, label


def _tr(text: str) -> str:
    return QCoreApplication.translate("Stamps", text)


PRESETS = (
    ("approved", "#18794E"), ("rejected", "#CE2C31"), ("reviewed", "#0D74CE"), ("draft", "#5B5BD6"),
    ("confidential", "#CE2C31"), ("final", "#18794E"), ("pending", "#CC4E00"), ("received", "#0D74CE"),
    ("paid", "#18794E"), ("urgent", "#CE2C31"), ("copy", "#60646C"), ("void", "#CE2C31"),
)
"""Tipo y color de tinta de los sellos predefinidos (el texto, en preset_text)."""

STAMP_COLORS = ("#18794E", "#0D74CE", "#5B5BD6", "#CE2C31", "#CC4E00", "#60646C")
"""Tintas para los sellos personalizados: como las de un tampón, oscuras y legibles."""


def preset_text(kind: str) -> str:
    texts = {
        "approved": _tr("Aprobado"), "rejected": _tr("Rechazado"), "reviewed": _tr("Revisado"),
        "draft": _tr("Borrador"), "confidential": _tr("Confidencial"), "final": _tr("Final"),
        "pending": _tr("Pendiente"), "received": _tr("Recibido"), "paid": _tr("Pagado"),
        "urgent": _tr("Urgente"), "copy": _tr("Copia"), "void": _tr("Anulado"),
    }
    return texts.get(kind, kind)


@dataclass
class StampChoice:
    kind: str = "approved"
    text: str = ""
    """Solo en los personalizados; los predefinidos se escriben en el idioma de la interfaz."""
    color: str = "#18794E"
    details: bool = True
    """Añadir debajo el nombre de quien sella y la fecha."""

    @property
    def label(self) -> str:
        return self.text if self.kind == "custom" else preset_text(self.kind)

    def rgb(self) -> tuple[float, float, float]:
        color = QColor(self.color)
        return color.redF(), color.greenF(), color.blueF()

    def subtitle(self) -> str:
        if not self.details:
            return ""
        when = QLocale().toString(QDateTime.currentDateTime(), QLocale.FormatType.ShortFormat)
        return f"{settings().author_name} · {when}"

    @classmethod
    def load(cls) -> "StampChoice":
        stored = settings().stamp_choice()
        try:
            choice = cls(str(stored.get("kind", "approved")), str(stored.get("text", "")),
                         str(stored.get("color", "#18794E")), bool(stored.get("details", True)))
        except (TypeError, ValueError, AttributeError):
            return cls()
        if choice.kind == "custom" and not choice.text.strip():
            return cls()
        if choice.kind != "custom" and choice.kind not in dict(PRESETS):
            return cls()
        return choice

    def save(self) -> None:
        settings().set_stamp_choice(asdict(self))


def stamp_image(choice: StampChoice, height: float, dpr: float = 1.0, subtitle: str | None = None) -> QImage:
    """Imagen del sello tal como quedará en el PDF (se dibuja con el mismo código)."""
    text = choice.label
    sub = choice.subtitle() if subtitle is None else subtitle
    width, natural = core_annots.stamp_size(text, sub)
    doc = pymupdf.open()
    try:
        page = doc.new_page(width=width, height=natural)
        core_annots.add_stamp(page, page.rect, text, choice.rgb(), sub, choice.kind)
        zoom = max(height * dpr / natural, 0.05)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=True, annots=True)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGBA8888).copy()
    finally:
        doc.close()
    image.setDevicePixelRatio(dpr)
    return image


class StampTile(QToolButton):
    """Un sello del desplegable, dibujado sobre papel."""

    def __init__(self, choice: StampChoice, selected: bool, parent=None):
        super().__init__(parent)
        self.choice = choice
        self.selected = selected
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(choice.label)
        self.setFixedSize(QSize(148, 52))
        # Se dibuja ya al tamaño que cabe en la casilla: reducirlo después lo emborrona.
        width, height = core_annots.stamp_size(choice.label)
        self._image = stamp_image(choice, min(30.0, (148 - 24) * height / width), self.devicePixelRatioF(), subtitle="")

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setBrush(QColor("#FFFFFF"))
        border = theme.color("accent") if self.selected else (
            theme.color("border_strong") if self.underMouse() else theme.color("border"))
        painter.setPen(QPen(border, 2 if self.selected else 1))
        painter.drawRoundedRect(rect, 8, 8)
        size = self._image.deviceIndependentSize()
        x = rect.x() + (rect.width() - size.width()) / 2
        y = rect.y() + (rect.height() - size.height()) / 2
        painter.drawImage(QRectF(x, y, size.width(), size.height()), self._image)

    def enterEvent(self, event) -> None:
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self.update()
        super().leaveEvent(event)


class StampPopup(QFrame):
    """Desplegable de la barra de anotación con todos los sellos."""

    picked = Signal(object)

    def __init__(self, current: StampChoice, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("FloatingBar")
        self.current = current
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        layout.addWidget(SectionLabel(_tr("Sellos")))
        grid = QGridLayout()
        grid.setSpacing(6)
        choices = [StampChoice(kind, "", color, current.details) for kind, color in PRESETS]
        choices += [StampChoice("custom", str(item["text"]), str(item.get("color", "#0D74CE")), current.details)
                    for item in settings().custom_stamps()]
        for index, choice in enumerate(choices):
            selected = choice.kind == current.kind and (choice.kind != "custom" or choice.text == current.text)
            tile = StampTile(choice, selected, self)
            tile.clicked.connect(lambda _=False, c=choice: self._choose(c))
            grid.addWidget(tile, index // 3, index % 3)
        layout.addLayout(grid)
        self.details = QCheckBox(_tr("Añadir mi nombre, la fecha y la hora"), self)
        self.details.setChecked(current.details)
        self.details.toggled.connect(self._details_changed)
        layout.addWidget(self.details)
        row = QHBoxLayout()
        custom = Button(_tr("Sello personalizado..."), "ghost", "plus", self)
        custom.clicked.connect(self._custom)
        row.addWidget(custom)
        row.addStretch(1)
        if settings().custom_stamps():
            clear = Button(_tr("Borrar los personalizados"), "ghost", "", self)
            clear.clicked.connect(self._clear_custom)
            row.addWidget(clear)
        layout.addLayout(row)

    def _choose(self, choice: StampChoice) -> None:
        choice.details = self.details.isChecked()
        self.picked.emit(choice)
        self.close()

    def _details_changed(self, checked: bool) -> None:
        self.current.details = checked
        self.picked.emit(StampChoice(self.current.kind, self.current.text, self.current.color, checked))

    def _custom(self) -> None:
        parent = self.parentWidget()
        self.close()
        dialog = CustomStampDialog(parent)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            choice = dialog.choice()
            choice.details = self.details.isChecked()
            stored = [s for s in settings().custom_stamps() if str(s.get("text")) != choice.text]
            settings().set_custom_stamps([{"text": choice.text, "color": choice.color}] + stored)
            self.picked.emit(choice)

    def _clear_custom(self) -> None:
        settings().set_custom_stamps([])
        if self.current.kind == "custom":
            self.picked.emit(StampChoice(details=self.details.isChecked()))
        self.close()

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 8))
        self.move(pos.x() - self.width() // 2 + anchor.width() // 2, pos.y())
        self.show()


class CustomStampDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Sello personalizado"))
        self.setMinimumWidth(420)
        self._color = STAMP_COLORS[1]
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)
        root.addWidget(label(self.tr("Sello personalizado"), "title"))
        root.addWidget(SectionLabel(self.tr("Texto")))
        self.text = QLineEdit(self)
        self.text.setPlaceholderText(self.tr("Por ejemplo, Visto bueno"))
        self.text.setMaxLength(40)
        self.text.textChanged.connect(self._refresh)
        root.addWidget(self.text)
        root.addWidget(SectionLabel(self.tr("Color")))
        swatches = QHBoxLayout()
        swatches.setSpacing(6)
        self._swatches: list[ColorSwatch] = []
        for value in STAMP_COLORS:
            swatch = ColorSwatch(value, self, 26)
            swatch.clicked.connect(lambda _=False, v=value: self._pick(v))
            swatches.addWidget(swatch)
            self._swatches.append(swatch)
        swatches.addStretch(1)
        root.addLayout(swatches)
        self.preview = label("", "")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(72)
        root.addWidget(self.preview)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = Button(self.tr("Cancelar"), "ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok = Button(self.tr("Usar este sello"), "primary")
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        root.addLayout(row)
        self._pick(self._color)

    def _pick(self, value: str) -> None:
        self._color = value
        for swatch, color in zip(self._swatches, STAMP_COLORS):
            swatch.selected = color == value
            swatch.update()
        self._refresh()

    def choice(self) -> StampChoice:
        return StampChoice("custom", " ".join(self.text.text().split()), self._color)

    def _refresh(self) -> None:
        choice = self.choice()
        self.ok.setEnabled(bool(choice.text))
        if not choice.text:
            self.preview.clear()
            return
        image = stamp_image(choice, 52, self.devicePixelRatioF(), subtitle="")
        self.preview.setPixmap(QPixmap.fromImage(image))
