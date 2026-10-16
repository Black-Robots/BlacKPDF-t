"""Acerca de BlackPDF y lista de atajos de teclado."""

from __future__ import annotations

import sys

import pymupdf
from PySide6 import __version__ as pyside_version
from PySide6.QtCore import Qt, qVersion
from PySide6.QtWidgets import QDialog, QGridLayout, QHBoxLayout, QLabel, QScrollArea, QVBoxLayout, QWidget

from ... import __version__
from .. import icons
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


SHORTCUTS = (
    ("Archivo", (
        ("Ctrl+O", "Abrir"), ("Ctrl+S", "Guardar"), ("Ctrl+Mayús+S", "Guardar como"), ("Ctrl+P", "Imprimir"),
        ("Ctrl+W", "Cerrar pestaña"), ("Ctrl+Tab", "Pestaña siguiente"), ("Ctrl+K", "Paleta de comandos"),
    )),
    ("Edición", (
        ("Ctrl+Z", "Deshacer"), ("Ctrl+Y", "Rehacer"), ("Ctrl+C", "Copiar texto"), ("Ctrl+A", "Seleccionar texto de la página"),
        ("Supr", "Eliminar páginas seleccionadas"), ("Ctrl+Mayús+R", "Girar a la derecha"), ("Ctrl+Mayús+L", "Girar a la izquierda"),
    )),
    ("Vista", (
        ("Ctrl++  /  +", "Acercar"), ("Ctrl+-  /  -", "Alejar"), ("Ctrl+0", "Tamaño real"), ("Ctrl+1", "Ajustar al ancho"),
        ("Ctrl+2", "Página completa"), ("Ctrl+rueda", "Zoom con el ratón"), ("Ctrl+F", "Buscar"), ("Ctrl+M", "Modo noche"),
        ("F4", "Panel lateral"), ("F11", "Pantalla completa"), ("Flechas", "Página anterior / siguiente"),
    )),
    ("Anotar", (
        ("V", "Seleccionar texto"), ("H", "Mano"), ("P", "Lápiz"), ("R", "Resaltar"), ("U", "Subrayar"),
        ("T", "Tachar"), ("X", "Cuadro de texto"), ("N", "Nota"), ("F", "Firma"), ("E", "Goma"),
        ("Ctrl+E", "Mostrar u ocultar la barra de anotación"), ("Esc", "Volver a seleccionar"),
    )),
)


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
