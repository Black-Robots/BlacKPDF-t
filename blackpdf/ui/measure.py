"""Medir: cifras con el formato del idioma, elegir la escala y calibrarla con una medida conocida."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QLocale, QPoint, Qt, Signal
from PySide6.QtWidgets import QComboBox, QDialog, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ..core import measure as core
from .widgets import Button, SectionLabel, SegmentedControl, label

RATIOS = (1, 2, 5, 10, 20, 25, 50, 75, 100, 200, 250, 500, 1000, 2000, 5000)
UNITS = ("mm", "cm", "m", "km", "in", "ft")


def _tr(text: str) -> str:
    return QCoreApplication.translate("Measure", text)


def mode_names() -> dict[str, str]:
    return {"distance": _tr("Distancia"), "perimeter": _tr("Perímetro"), "area": _tr("Área")}


def format_measure(value: float, unit: str, area: bool = False) -> str:
    """«4,25 m», «12,8 m²», «1.250 mm»: con los decimales justos y el separador del idioma."""
    # Lo que se usa en cada unidad: centímetros en metros, milímetros enteros...
    if area:
        decimals = {"mm": 0, "cm": 0, "m": 2, "km": 4}.get(unit, 2)
    else:
        decimals = {"mm": 0, "cm": 1, "m": 2, "km": 3}.get(unit, 2)
    number = QLocale().toString(float(value), "f", decimals)
    return f"{number} {unit}{'²' if area else ''}"


def scale_text(scale: core.Scale) -> str:
    return f"{scale.ratio} · {scale.unit}" if scale.ratio else scale.unit


class ScalePopup(QFrame):
    """Desplegable de la barra con las escalas habituales de plano y la unidad."""

    picked = Signal(object)
    calibrateRequested = Signal()

    def __init__(self, current: core.Scale, from_document: core.Scale | None, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setObjectName("FloatingBar")
        self.current = current
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        if from_document is not None:
            use = Button(self.tr("La del documento: {0}").format(scale_text(from_document)), "ghost", "file-text", self)
            use.clicked.connect(lambda: self._choose(from_document))
            layout.addWidget(use)
        layout.addWidget(SectionLabel(self.tr("Escala del plano")))
        grid = QGridLayout()
        grid.setSpacing(4)
        current_ratio = current.ratio
        for index, ratio in enumerate(RATIOS):
            button = Button(f"1:{ratio}", "primary" if current_ratio == f"1:{ratio}" else "ghost", "", self)
            button.clicked.connect(lambda _=False, r=ratio: self._choose_ratio(r))
            grid.addWidget(button, index // 5, index % 5)
        layout.addLayout(grid)
        layout.addWidget(SectionLabel(self.tr("Unidad")))
        self.units = SegmentedControl([(unit, unit) for unit in UNITS], self)
        self.units.set_value(current.unit if current.unit in UNITS else "m", emit=False)
        self.units.changed.connect(self._unit_changed)
        layout.addWidget(self.units)
        calibrate = Button(self.tr("Calibrar con una medida conocida..."), "", "ruler", self)
        calibrate.setToolTip(self.tr("Traza una línea sobre algo cuya medida sepas (una cota, una puerta...)"))
        calibrate.clicked.connect(self._calibrate)
        layout.addWidget(calibrate)

    def _choose(self, scale: core.Scale) -> None:
        self.picked.emit(scale)
        self.close()

    def _choose_ratio(self, ratio: int) -> None:
        self._choose(core.Scale.from_ratio(ratio, self.units.value()))

    def _unit_changed(self, unit: str) -> None:
        self.current = self.current.with_unit(unit) if self.current.unit in core.UNITS_MM else self.current
        self.picked.emit(self.current)

    def _calibrate(self) -> None:
        self.calibrateRequested.emit()
        self.close()

    def show_below(self, anchor: QWidget) -> None:
        self.adjustSize()
        pos = anchor.mapToGlobal(QPoint(0, anchor.height() + 8))
        self.move(pos.x() - self.width() // 2 + anchor.width() // 2, pos.y())
        self.show()


class CalibrateDialog(QDialog):
    """¿Cuánto mide en la realidad la línea que se acaba de trazar?"""

    def __init__(self, length_pt: float, unit: str = "m", parent=None):
        super().__init__(parent)
        self.length_pt = length_pt
        self.setWindowTitle(self.tr("Calibrar la escala"))
        self.setMinimumWidth(420)
        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 18)
        root.setSpacing(12)
        root.addWidget(label(self.tr("Calibrar la escala"), "title"))
        root.addWidget(label(self.tr(
            "Escribe cuánto mide en la realidad la línea que has trazado. Las medidas de este documento "
            "usarán esa escala."), "muted", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        self.value = QDoubleSpinBox(self)
        self.value.setRange(0.001, 1_000_000)
        self.value.setDecimals(3)
        self.value.setValue(1.0)
        self.value.selectAll()
        row.addWidget(self.value, 1)
        self.unit = QComboBox(self)
        for name in UNITS:
            self.unit.addItem(name, name)
        self.unit.setCurrentIndex(max(0, self.unit.findData(unit)))
        row.addWidget(self.unit)
        root.addLayout(row)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = Button(self.tr("Cancelar"), "ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        ok = Button(self.tr("Usar esta escala"), "primary")
        ok.clicked.connect(self.accept)
        ok.setDefault(True)
        buttons.addWidget(ok)
        root.addLayout(buttons)

    def scale(self) -> core.Scale:
        return core.Scale.calibrated(self.length_pt, self.value.value(), self.unit.currentData())
