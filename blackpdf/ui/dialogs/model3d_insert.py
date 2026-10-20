"""Insertar modelo 3D: archivo, dónde va la vista, fondo y si se adjunta el original."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QVBoxLayout

from ...core.model3d.insert import INSERTABLE_EXTENSIONS
from ...core.model3d.meshes import CAD_EXTENSIONS, cad_available
from ..settings import settings
from ..widgets import Button, PathPicker, SectionLabel, SegmentedControl, label
from .base import IconTile

BACKGROUNDS = {"white": (1.0, 1.0, 1.0), "gray": (0.86, 0.87, 0.89), "dark": (0.11, 0.11, 0.13)}


def _tr(text: str) -> str:
    return QCoreApplication.translate("Insert3DDialog", text)


def model_filter() -> str:
    patterns = " ".join(f"*{ext}" for ext in INSERTABLE_EXTENSIONS)
    return ";;".join((_tr("Modelos 3D y CAD ({0})").format(patterns), _tr("Todos los archivos (*)")))


@dataclass
class Insert3DOptions:
    path: str
    placement: str
    """"rect" (recuadro en la página) o "page" (página nueva)."""
    background: tuple[float, float, float]
    attach_original: bool


class Insert3DDialog(QDialog):
    def __init__(self, parent=None, path: str = "", already_attached: bool = False):
        super().__init__(parent)
        self._already_attached = already_attached
        self.setWindowTitle(self.tr("Insertar modelo 3D"))
        self.setModal(True)
        self.setMinimumWidth(560)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(IconTile("box"), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(label(self.tr("Insertar modelo 3D"), "title"))
        texts.addWidget(label(self.tr(
            "Mete una pieza en el PDF para verla en 3D en BlackPDF y en Acrobat. Admite STEP, IGES, STL, OBJ, "
            "3MF, glTF, PRC y U3D."), "muted", wrap=True))
        header.addLayout(texts, 1)
        root.addLayout(header)

        root.addWidget(SectionLabel(self.tr("Archivo")))
        self.picker = PathPicker("open", model_filter(), self, self.tr("Elige la pieza o el conjunto"))
        self.picker.set_path(path)
        self.picker.changed.connect(self._refresh)
        root.addWidget(self.picker)
        self.cad_note = label("", "caption", wrap=True)
        self.cad_note.hide()
        root.addWidget(self.cad_note)

        root.addWidget(SectionLabel(self.tr("Dónde va la vista 3D")))
        self.placement = SegmentedControl(
            [("rect", self.tr("Recuadro en esta página")), ("page", self.tr("Página nueva"))], self,
            icons_map={"rect": "square-dashed", "page": "file-plus-2"})
        self.placement.set_value(str(settings().value("insert3d/placement", "rect")), emit=False)
        self.placement.changed.connect(self._refresh)
        root.addWidget(self.placement)

        root.addWidget(SectionLabel(self.tr("Fondo")))
        self.background = SegmentedControl(
            [("white", self.tr("Blanco")), ("gray", self.tr("Gris")), ("dark", self.tr("Oscuro"))], self)
        self.background.set_value(str(settings().value("insert3d/background", "white")), emit=False)
        root.addWidget(self.background)

        self.attach = QCheckBox(self.tr("Adjuntar también el archivo original al PDF"), self)
        self.attach.setToolTip(self.tr("Quien reciba el PDF podrá sacar la pieza tal cual (por ejemplo, el .step)."))
        self.attach.setChecked(not already_attached
                               and str(settings().value("insert3d/attach", "true")).lower() in ("1", "true"))
        root.addWidget(self.attach)

        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = Button(self.tr("Cancelar"), "ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok = Button("", "primary")
        self.ok.setDefault(True)
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        root.addLayout(row)
        self._refresh()

    def _refresh(self, *_args) -> None:
        path = self.picker.path()
        extension = Path(path).suffix.lower()
        valid = bool(path) and Path(path).is_file() and extension in INSERTABLE_EXTENSIONS
        missing_cad = extension in CAD_EXTENSIONS and not cad_available()
        self.cad_note.setVisible(missing_cad or (bool(path) and not valid))
        if missing_cad:
            self.cad_note.setText(self.tr("Para STEP e IGES hace falta el componente de CAD de BlackPDF, que viene "
                                          "en los instaladores. Exporta la pieza como STL u OBJ o reinstala BlackPDF."))
        elif path and not valid:
            self.cad_note.setText(self.tr("Elige un archivo STEP, IGES, STL, OBJ, 3MF, glTF, PRC o U3D."))
        self.ok.setEnabled(valid and not missing_cad)
        self.ok.setText(self.tr("Dibujar el recuadro") if self.placement.value() == "rect" else self.tr("Insertar"))

    def options(self) -> Insert3DOptions:
        return Insert3DOptions(self.picker.path(), self.placement.value(), BACKGROUNDS[self.background.value()],
                               self.attach.isChecked())

    def done(self, result: int) -> None:
        if result == QDialog.DialogCode.Accepted:
            settings().set_value("insert3d/placement", self.placement.value())
            settings().set_value("insert3d/background", self.background.value())
            if not self._already_attached:
                settings().set_value("insert3d/attach", self.attach.isChecked())
            if self.picker.path() and not self._already_attached:
                settings().last_directory = self.picker.path()
        super().done(result)
