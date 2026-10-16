"""Herramientas de conversión y optimización."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QComboBox, QFrame, QHBoxLayout, QSlider, QVBoxLayout, QWidget

from ...core.files import human_size, unique_path
from ...core.images import IMAGE_EXTENSIONS
from .. import theme
from ..widgets import FileListWidget, PageRangeEdit, SegmentedControl, image_filter, label, pdf_filter
from .base import OutputPicker, ToolDialog, ValidationError
from .organize import _SingleSourceDialog


class ChoiceCard(QFrame):
    clicked = Signal(str)

    def __init__(self, key: str, title: str, detail: str, parent=None):
        super().__init__(parent)
        self.key = key
        self.selected = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(4)
        layout.addWidget(label(title, "cardtitle"))
        layout.addWidget(label(detail, "caption", wrap=True))
        self.setMinimumHeight(84)

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        rect = self.rect().adjusted(1, 1, -1, -1)
        painter.setPen(QPen(p.qcolor("accent") if self.selected else p.qcolor("border_strong"), 2 if self.selected else 1))
        painter.setBrush(p.qcolor("accent_soft") if self.selected else p.qcolor("surface_alt"))
        painter.drawRoundedRect(rect, theme.RADIUS, theme.RADIUS)


class ChoiceCards(QWidget):
    changed = Signal(str)

    def __init__(self, options: list[tuple[str, str, str]], parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        self.cards = []
        for key, title, detail in options:
            card = ChoiceCard(key, title, detail, self)
            card.clicked.connect(self.set_value)
            layout.addWidget(card, 1)
            self.cards.append(card)
        self._value = ""

    def value(self) -> str:
        return self._value

    def set_value(self, key: str) -> None:
        self._value = key
        for card in self.cards:
            card.selected = card.key == key
            card.update()
        self.changed.emit(key)


class CompressDialog(_SingleSourceDialog):
    icon_name = "shrink"
    output_suffix = "comprimido"

    def title(self):
        return self.tr("Comprimir PDF")

    def description(self):
        return self.tr("Reduce el tamaño del archivo optimizando imágenes, fuentes y estructura.")

    def action_text(self):
        return self.tr("Comprimir")

    def build(self):
        self.add_source()
        self.level = ChoiceCards([
            ("low", self.tr("Baja"), self.tr("Sin pérdida de calidad. Limpia y reorganiza el archivo.")),
            ("medium", self.tr("Recomendada"), self.tr("Imágenes a 150 ppp. Buena calidad en pantalla e impresión.")),
            ("high", self.tr("Extrema"), self.tr("Imágenes a 96 ppp. El tamaño mínimo para enviar por correo.")),
        ])
        self.level.set_value("medium")
        self.add_field(self.tr("Nivel de compresión"), self.level)
        self.add_output()
        self.finish_build()

    def job(self):
        source = self.require_source()
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.compress:compress_pdf", (source, output, self.level.value()), self.common_kwargs()

    def describe_result(self, result):
        before, after = result.original_size, result.compressed_size
        if result.saved_ratio < 0.01:
            detail = self.tr("El archivo ya estaba optimizado y no se ha podido reducir más ({0}).").format(human_size(after))
        else:
            detail = self.tr("{0} → {1}  ·  {2} % más pequeño").format(
                human_size(before), human_size(after), round(result.saved_ratio * 100)
            )
        return self.tr("PDF comprimido"), detail, [result.path]


class PdfToImagesDialog(_SingleSourceDialog):
    icon_name = "image"
    output_mode = "folder"
    output_suffix = "imagenes"

    def title(self):
        return self.tr("PDF a imagen")

    def description(self):
        return self.tr("Convierte cada página en una imagen PNG o JPG.")

    def action_text(self):
        return self.tr("Convertir")

    def build(self):
        self.add_source()
        self.format = SegmentedControl([("png", "PNG"), ("jpg", "JPG")])
        self.resolution = QComboBox()
        for dpi, name in ((72, self.tr("Pantalla (72 ppp)")), (150, self.tr("Estándar (150 ppp)")),
                          (300, self.tr("Alta (300 ppp)")), (600, self.tr("Máxima (600 ppp)"))):
            self.resolution.addItem(name, dpi)
        self.resolution.setCurrentIndex(1)
        self.add_row(self.field_block(self.tr("Formato"), self.format),
                     self.field_block(self.tr("Resolución"), self.resolution))
        self.quality = QSlider(Qt.Orientation.Horizontal)
        self.quality.setRange(40, 100)
        self.quality.setValue(90)
        self.quality_label = label("", "caption")
        quality_row = QWidget()
        row = QHBoxLayout(quality_row)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(self.quality, 1)
        row.addWidget(self.quality_label)
        self.quality.valueChanged.connect(lambda v: self.quality_label.setText(f"{v} %"))
        self.quality_label.setText("90 %")
        self.quality_block = self.add_field(self.tr("Calidad JPG"), quality_row)
        self.quality_block.setVisible(False)
        self.format.changed.connect(lambda key: self.quality_block.setVisible(key == "jpg"))
        self.ranges = PageRangeEdit(allow_empty=True)
        self.ranges.setText(self.preset.get("pages", ""))
        self.ranges.textChanged.connect(lambda _: self.refresh())
        self.add_field(self.tr("Páginas"), self.ranges, self.tr("Déjalo vacío para convertir todas."))
        self.add_output("folder")
        self.finish_build()

    def on_source_changed(self):
        self.ranges.set_page_count(self.source.page_count)

    def is_ready(self):
        return self.source.is_ready() and self.ranges.is_valid()

    def job(self):
        source = self.require_source()
        folder = self.output.path()
        if not folder:
            raise ValidationError(self.tr("Elige la carpeta de destino."))
        return "blackpdf.core.images:pdf_to_images", (source, folder), {
            "fmt": self.format.value(),
            "dpi": int(self.resolution.currentData()),
            "pages": self.ranges.text(),
            "jpg_quality": self.quality.value(),
            "base_name": self.source.stem(),
            **self.common_kwargs(),
        }

    def describe_result(self, result):
        count = len(result)
        detail = self.tr("Se ha creado 1 imagen.") if count == 1 else self.tr("Se han creado {0} imágenes.").format(count)
        if result:
            detail += " " + self.tr("Carpeta: {0}").format(Path(result[0]).parent)
        return self.tr("Conversión terminada"), detail, [Path(p) for p in result]


class ImagesToPdfDialog(ToolDialog):
    icon_name = "images"
    output_suffix = "imagenes"

    def title(self):
        return self.tr("Imagen a PDF")

    def description(self):
        return self.tr("Convierte fotos y escaneos (JPG, PNG, TIFF...) en un único PDF, una imagen por página.")

    def action_text(self):
        return self.tr("Crear PDF")

    def build(self):
        self.files = FileListWidget(IMAGE_EXTENSIONS, image_filter(), self)
        self.files.changed.connect(self._files_changed)
        self.add_field(self.tr("Imágenes"), self.files)
        self.page_size = QComboBox()
        for key, name in (("original", self.tr("Tamaño de la imagen")), ("A4", "A4"), ("letter", self.tr("Carta")),
                          ("A3", "A3"), ("A5", "A5"), ("legal", "Legal")):
            self.page_size.addItem(name, key)
        self.page_size.setCurrentIndex(1)
        self.orientation = SegmentedControl(
            [("auto", self.tr("Automática")), ("portrait", self.tr("Vertical")), ("landscape", self.tr("Horizontal"))]
        )
        self.add_row(self.field_block(self.tr("Tamaño de página"), self.page_size),
                     self.field_block(self.tr("Orientación"), self.orientation))
        self.margin = SegmentedControl([("0", self.tr("Sin margen")), ("18", self.tr("Pequeño")), ("42", self.tr("Grande"))])
        self.margin.set_value("18")
        self.add_field(self.tr("Margen"), self.margin)
        self.page_size.currentIndexChanged.connect(self._size_changed)
        self.output = OutputPicker("save", pdf_filter(), self)
        self.add_field(self.tr("Guardar como"), self.output)
        for path in self.preset.get("files", []):
            self.files.add_files([path])

    def _size_changed(self):
        original = self.page_size.currentData() == "original"
        self.orientation.setEnabled(not original)

    def _files_changed(self):
        files = self.files.files()
        if files:
            first = Path(files[0])
            self.output.suggest(str(unique_path(first.parent / f"{first.stem}.pdf")))
        self.refresh()

    def is_ready(self):
        return bool(self.files.files())

    def job(self):
        files = self.files.files()
        if not files:
            raise ValidationError(self.tr("Añade al menos una imagen."))
        output = self.require_output(self.output)
        return "blackpdf.core.images:images_to_pdf", (files, output), {
            "page_size": self.page_size.currentData(),
            "orientation": self.orientation.value() if self.page_size.currentData() != "original" else "auto",
            "margin": float(self.margin.value()),
        }

    def sizeHint(self):
        return QSize(640, 640)

