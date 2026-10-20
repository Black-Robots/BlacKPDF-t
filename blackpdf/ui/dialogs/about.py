"""Acerca de BlackPDF y lista de atajos de teclado."""

from __future__ import annotations

import sys

import pymupdf
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QT_TRANSLATE_NOOP, Qt, qVersion
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ... import __version__
from .. import icons
from ..keys import shortcut_text
from ..widgets import Button, SectionLabel, label


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Acerca de BlackPDF"))
        self.setFixedWidth(500)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(32, 32, 32, 24)
        layout.setSpacing(12)
        logo = QLabel()
        logo.setPixmap(icons.app_logo(72, self.devicePixelRatioF()))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        name = label("BlackPDF", "hero")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name)
        version = label(self.tr("Versión {0}").format(__version__), "muted")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(version)
        text = label(self.tr(
            "Visor y editor de PDF libre y gratuito. Funciona sin conexión: tus documentos nunca salen de tu equipo."),
            "body", wrap=True)
        text.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(text)
        layout.addSpacing(8)
        layout.addWidget(SectionLabel(self.tr("Componentes")))
        credits = label(
            f"Python {sys.version.split()[0]}  ·  Qt {qVersion()} / PySide6 {pyside_version}  ·  "
            f"PyMuPDF {pymupdf.VersionBind} (MuPDF {pymupdf.VersionFitz})<br>"
            + self.tr("Tipografía Inter (SIL OFL)  ·  Iconos Lucide (ISC)  ·  Datos de Tesseract (Apache 2.0)"),
            "caption", wrap=True)
        layout.addWidget(credits)
        layout.addWidget(SectionLabel(self.tr("Licencia")))
        layout.addWidget(label(self.tr(
            "BlackPDF es software libre: puedes usarlo, estudiarlo, compartirlo y modificarlo según los "
            "términos de la Licencia Pública General Affero de GNU, versión 3 (AGPL-3.0)."), "caption", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        close = Button(self.tr("Cerrar"), "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addSpacing(8)
        layout.addLayout(row)


def _n(text: str) -> str:
    return QT_TRANSLATE_NOOP("ShortcutsDialog", text)


# (teclas en formato portable de Qt o texto traducible, acción)
SHORTCUTS = (
    (_n("Archivo"), (
        ("Ctrl+O", _n("Abrir")), ("Ctrl+S", _n("Guardar")), ("Ctrl+Shift+S", _n("Guardar como")),
        ("Ctrl+P", _n("Imprimir")), ("Ctrl+W", _n("Cerrar pestaña")), ("Ctrl+Tab", _n("Pestaña siguiente")),
        ("Ctrl+K", _n("Paleta de comandos")),
    )),
    (_n("Edición"), (
        ("Ctrl+Z", _n("Deshacer")), ("Ctrl+Y", _n("Rehacer")), ("Ctrl+C", _n("Copiar texto")),
        ("Ctrl+A", _n("Seleccionar texto de la página")), ("Del", _n("Eliminar páginas seleccionadas")),
        ("Ctrl+Shift+R", _n("Girar a la derecha")), ("Ctrl+Shift+L", _n("Girar a la izquierda")),
    )),
    (_n("Vista"), (
        ("Ctrl++  /  +", _n("Acercar")), ("Ctrl+-  /  -", _n("Alejar")), ("Ctrl+0", _n("Tamaño real")),
        ("Ctrl+1", _n("Ajustar al ancho")), ("Ctrl+2", _n("Página completa")),
        (_n("Ctrl+rueda"), _n("Zoom con el ratón")), ("Ctrl+F", _n("Buscar")), ("Ctrl+M", _n("Modo noche")),
        ("F4", _n("Panel lateral")), ("F11", _n("Pantalla completa")),
        (_n("Flechas"), _n("Página anterior / siguiente")),
    )),
    (_n("Anotar"), (
        ("V", _n("Seleccionar texto")), ("H", _n("Mano")), ("P", _n("Lápiz")), ("R", _n("Resaltar")),
        ("U", _n("Subrayar")), ("T", _n("Tachar")), ("X", _n("Cuadro de texto")), ("N", _n("Nota")),
        ("F", _n("Firma")), ("E", _n("Goma")), ("Ctrl+E", _n("Mostrar u ocultar la barra de anotación")),
        ("Esc", _n("Volver a seleccionar")),
    )),
)

_TEXT_KEYS = {"Ctrl+rueda", "Flechas"}


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Atajos de teclado"))
        self.resize(720, 620)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 16)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(28, 24, 28, 8)
        layout.setSpacing(14)
        layout.addWidget(label(self.tr("Atajos de teclado"), "title"))
        for section, items in SHORTCUTS:
            layout.addWidget(SectionLabel(self.tr(section)))
            grid = QGridLayout()
            grid.setHorizontalSpacing(16)
            grid.setVerticalSpacing(8)
            for row, (keys, action) in enumerate(items):
                if keys in _TEXT_KEYS:
                    keys = self.tr(keys)
                elif "/" not in keys:
                    keys = shortcut_text(keys)
                key_label = label(keys, "kbd")
                key_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                grid.addWidget(key_label, row, 0, Qt.AlignmentFlag.AlignLeft)
                grid.addWidget(label(self.tr(action), "body"), row, 1)
            grid.setColumnStretch(1, 1)
            grid.setColumnMinimumWidth(0, 150)
            layout.addLayout(grid)
        layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        row = QHBoxLayout()
        row.setContentsMargins(28, 0, 28, 0)
        row.addStretch(1)
        close = Button(self.tr("Cerrar"), "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)
