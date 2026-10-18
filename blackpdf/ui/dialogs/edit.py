"""Herramientas de edición: marca de agua, números, recorte, tamaño y aplanado."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import pymupdf

from ...core.geometry import MM, Margins, auto_crop_page, crop_page, resize_document
from ...core.numbering import FORMAT_PRESETS, PageNumberOptions, apply_page_numbers
from ...core.text import FONT_CHOICES
from ...core.watermark import WatermarkOptions, apply_watermark
from .. import theme
from ..interaction import PRESET_COLORS
from ..widgets import AutoStack, ColorButton, PageRangeEdit, PathPicker, SegmentedControl, image_filter, label
from .base import PagePreview, ValidationError, preview_page
from .organize import _SingleSourceDialog


def _rgb(value: str) -> tuple[float, float, float]:
    color = QColor(value)
    return color.redF(), color.greenF(), color.blueF()


class PositionPicker(QWidget):
    """Mini página con los puntos de anclaje seleccionables."""

    changed = Signal(str)

    def __init__(self, positions: list[list[str]], value: str, parent=None):
        super().__init__(parent)
        self.positions = positions
        self._value = value
        self.setFixedSize(96, 120)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def value(self) -> str:
        return self._value

    def set_value(self, value: str) -> None:
        self._value = value
        self.update()
        self.changed.emit(value)

    def _spots(self):
        rows = len(self.positions)
        page = QRectF(8, 6, self.width() - 16, self.height() - 12)
        result = []
        for r, row in enumerate(self.positions):
            cols = len(row)
            for c, key in enumerate(row):
                if not key:
                    continue
                x = page.left() + 14 + c * (page.width() - 28) / max(cols - 1, 1)
                if rows == 1:
                    y = page.center().y()
                else:
                    y = page.top() + 14 + r * (page.height() - 28) / max(rows - 1, 1)
                result.append((key, QPointF(x, y)))
        return page, result

    def mousePressEvent(self, event) -> None:
        _, spots = self._spots()
        pos = event.position()
        best = min(spots, key=lambda s: (s[1] - pos).manhattanLength(), default=None)
        if best is not None and (best[1] - pos).manhattanLength() < 28:
            self.set_value(best[0])

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        page, spots = self._spots()
        painter.setPen(QPen(p.qcolor("border_strong"), 1))
        painter.setBrush(p.qcolor("surface_alt"))
        painter.drawRoundedRect(page, 4, 4)
        for key, point in spots:
            selected = key == self._value
            painter.setPen(QPen(p.qcolor("accent") if selected else p.qcolor("text_faint"), 1.5))
            painter.setBrush(p.qcolor("accent") if selected else Qt.BrushStyle.NoBrush)
            painter.drawEllipse(point, 6 if selected else 5, 6 if selected else 5)


class _PreviewDialog(_SingleSourceDialog):
    """Formulario a la izquierda y vista previa de la primera página a la derecha."""

    def begin_columns(self) -> None:
        self._main_form = self.form
        holder = QWidget()
        columns = QHBoxLayout(holder)
        columns.setContentsMargins(0, 0, 0, 0)
        columns.setSpacing(24)
        left = QVBoxLayout()
        left.setSpacing(16)
        columns.addLayout(left, 1)
        right = QVBoxLayout()
        right.setSpacing(6)
        self.preview = PagePreview(self.render_preview, self, 260)
        right.addWidget(self.preview)
        caption = label(self.preview_caption(), "caption")
        caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right.addWidget(caption)
        right.addStretch(1)
        columns.addLayout(right)
        self._main_form.addWidget(holder)
        self.form = left

    def preview_caption(self) -> str:
        return self.tr("Vista previa de la primera página")

    def end_columns(self) -> None:
        self.form.addStretch(1)
        self.form = self._main_form

    def on_source_changed(self):
        if hasattr(self, "ranges"):
            self.ranges.set_page_count(self.source.page_count)
        self.update_preview()

    def update_preview(self, *_):
        if hasattr(self, "preview"):
            self.preview.schedule()

    def render_preview(self):
        doc = preview_page(self.source.preview_document())
        if doc is None:
            return None
        try:
            self.apply_to_preview(doc)
        except Exception:
            pass
        page = doc[0]
        scale = self.preview.render_scale(page.rect)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    def apply_to_preview(self, doc: pymupdf.Document) -> None:
        pass

    def sizeHint(self):
        return QSize(860, 720)


class WatermarkDialog(_PreviewDialog):
    icon_name = "stamp"
    output_suffix = "marca_agua"

    def title(self):
        return self.tr("Marca de agua")

    def description(self):
        return self.tr("Estampa un texto o una imagen en las páginas, con la opacidad y el giro que quieras.")

    def action_text(self):
        return self.tr("Añadir marca de agua")

    def build(self):
        self.add_source()
        self.begin_columns()
        self.kind = SegmentedControl([("text", self.tr("Texto")), ("image", self.tr("Imagen"))])
        self.add_field(self.tr("Tipo"), self.kind)
        self.kind_stack = AutoStack()
        # Texto
        text_box = QWidget()
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(12)
        self.text = QPlainTextEdit(self.tr("CONFIDENCIAL"))
        self.text.setFixedHeight(64)
        text_layout.addWidget(self.field_block(self.tr("Texto"), self.text))
        self.font_box = QComboBox()
        for key, name in FONT_CHOICES.items():
            self.font_box.addItem(name, key)
        self.font_box.setCurrentIndex(1)
        self.size_box = QSpinBox()
        self.size_box.setRange(6, 300)
        self.size_box.setValue(56)
        self.size_box.setSuffix(" pt")
        self.color = ColorButton("#E5484D", PRESET_COLORS)
        row = QWidget()
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(0, 0, 0, 0)
        row_layout.setSpacing(12)
        row_layout.addWidget(self.field_block(self.tr("Fuente"), self.font_box), 2)
        row_layout.addWidget(self.field_block(self.tr("Tamaño"), self.size_box), 1)
        row_layout.addWidget(self.field_block(self.tr("Color"), self.color), 0)
        text_layout.addWidget(row)
        # Imagen
        image_box = QWidget()
        image_layout = QVBoxLayout(image_box)
        image_layout.setContentsMargins(0, 0, 0, 0)
        image_layout.setSpacing(12)
        self.image = PathPicker("open", image_filter())
        image_layout.addWidget(self.field_block(self.tr("Imagen"), self.image))
        self.scale = QSlider(Qt.Orientation.Horizontal)
        self.scale.setRange(5, 100)
        self.scale.setValue(50)
        image_layout.addWidget(self.field_block(self.tr("Ancho respecto a la página"), self.scale))
        self.kind_stack.addWidget(text_box)
        self.kind_stack.addWidget(image_box)
        self.form.addWidget(self.kind_stack)
        self.kind.changed.connect(lambda key: (self.kind_stack.setCurrentIndex(0 if key == "text" else 1), self.refresh(), self.update_preview()))

        self.opacity = QSlider(Qt.Orientation.Horizontal)
        self.opacity.setRange(5, 100)
        self.opacity.setValue(30)
        self.rotation = QSpinBox()
        self.rotation.setRange(-180, 180)
        self.rotation.setValue(45)
        self.rotation.setSuffix("°")
        self.add_row(self.field_block(self.tr("Opacidad"), self.opacity), self.field_block(self.tr("Giro"), self.rotation))

        position_row = QWidget()
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(16)
        self.position = PositionPicker(
            [["top-left", "top", "top-right"], ["left", "center", "right"], ["bottom-left", "bottom", "bottom-right"]],
            "center",
        )
        position_layout.addWidget(self.position)
        options = QVBoxLayout()
        self.tile = QCheckBox(self.tr("Repetir en mosaico"))
        self.behind = QCheckBox(self.tr("Colocar detrás del contenido"))
        options.addWidget(self.tile)
        options.addWidget(self.behind)
        options.addStretch(1)
        position_layout.addLayout(options, 1)
        self.add_field(self.tr("Posición"), position_row)
        self.ranges = PageRangeEdit(allow_empty=True)
        self.ranges.setText(self.preset.get("pages", ""))
        self.add_field(self.tr("Páginas"), self.ranges, self.tr("Déjalo vacío para todas."))
        self.end_columns()
        self.add_output()

        for signal in (self.text.textChanged, self.font_box.currentIndexChanged, self.size_box.valueChanged,
                       self.color.colorChanged, self.image.changed, self.scale.valueChanged,
                       self.opacity.valueChanged, self.rotation.valueChanged, self.position.changed,
                       self.tile.toggled, self.behind.toggled):
            signal.connect(self.update_preview)
        self.text.textChanged.connect(self.refresh)
        self.image.changed.connect(lambda _: self.refresh())
        self.ranges.textChanged.connect(lambda _: self.refresh())
        self.finish_build()
        self.update_preview()

    def options(self) -> WatermarkOptions:
        is_text = self.kind.value() == "text"
        return WatermarkOptions(
            text=self.text.toPlainText().strip() if is_text else "",
            image_path=self.image.path() if not is_text else "",
            font=self.font_box.currentData(),
            font_size=float(self.size_box.value()),
            color=_rgb(self.color.color()),
            opacity=self.opacity.value() / 100,
            rotation=float(self.rotation.value()),
            position="tile" if self.tile.isChecked() else self.position.value(),
            image_scale=self.scale.value() / 100,
            behind=self.behind.isChecked(),
            pages=self.ranges.text(),
        )

    def is_ready(self):
        if not self.source.is_ready() or not self.ranges.is_valid():
            return False
        if self.kind.value() == "text":
            return bool(self.text.toPlainText().strip())
        return bool(self.image.path())

    def apply_to_preview(self, doc):
        options = self.options()
        options.pages = ""
        if options.text or options.image_path:
            apply_watermark(doc, options)

    def job(self):
        source = self.require_source()
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.watermark:add_watermark", (source, output, self.options()), self.common_kwargs()


class PageNumbersDialog(_PreviewDialog):
    icon_name = "hash"
    output_suffix = "numerado"

    def title(self):
        return self.tr("Números de página")

    def description(self):
        return self.tr("Numera las páginas con el formato y la posición que prefieras.")

    def action_text(self):
        return self.tr("Numerar páginas")

    def build(self):
        self.add_source()
        self.begin_columns()
        position_row = QWidget()
        position_layout = QHBoxLayout(position_row)
        position_layout.setContentsMargins(0, 0, 0, 0)
        position_layout.setSpacing(16)
        self.position = PositionPicker(
            [["top-left", "top-center", "top-right"], ["", "", ""], ["bottom-left", "bottom-center", "bottom-right"]],
            "bottom-center",
        )
        position_layout.addWidget(self.position)
        format_box = QVBoxLayout()
        format_box.setSpacing(8)
        self.format = QComboBox()
        for key, name in (("number", "1"), ("number-total", "1/10"), ("page", self.tr("Página 1")),
                          ("page-of", self.tr("Página 1 de 10")), ("custom", self.tr("Personalizado"))):
            self.format.addItem(name, key)
        format_box.addWidget(self.format)
        self.custom = QLineEdit(self.tr("Pág. {n}"))
        self.custom.setPlaceholderText("{n} / {total}")
        self.custom.setToolTip(self.tr("Usa {n} para el número y {total} para el total."))
        self.custom.setVisible(False)
        format_box.addWidget(self.custom)
        format_box.addStretch(1)
        position_layout.addLayout(format_box, 1)
        self.add_field(self.tr("Posición y formato"), position_row)
        self.format.currentIndexChanged.connect(lambda _: (self.custom.setVisible(self.format.currentData() == "custom"), self.update_preview(), self.refresh()))

        self.start = QSpinBox()
        self.start.setRange(0, 100000)
        self.start.setValue(1)
        self.size_box = QSpinBox()
        self.size_box.setRange(6, 72)
        self.size_box.setValue(11)
        self.size_box.setSuffix(" pt")
        self.margin = QSpinBox()
        self.margin.setRange(0, 100)
        self.margin.setValue(10)
        self.margin.setSuffix(" mm")
        self.add_row(self.field_block(self.tr("Empezar en"), self.start),
                     self.field_block(self.tr("Tamaño"), self.size_box),
                     self.field_block(self.tr("Margen"), self.margin))
        self.font_box = QComboBox()
        for key, name in FONT_CHOICES.items():
            self.font_box.addItem(name, key)
        self.color = ColorButton("#3F3F46", PRESET_COLORS)
        self.add_row(self.field_block(self.tr("Fuente"), self.font_box), self.field_block(self.tr("Color"), self.color))
        self.ranges = PageRangeEdit(allow_empty=True)
        self.ranges.setText(self.preset.get("pages", ""))
        self.add_field(self.tr("Páginas"), self.ranges, self.tr("Déjalo vacío para numerar todas."))
        self.skip_first = QCheckBox(self.tr("No numerar la primera página (portada)"))
        self.form.addWidget(self.skip_first)
        self.end_columns()
        self.add_output()
        for signal in (self.position.changed, self.custom.textChanged, self.start.valueChanged, self.size_box.valueChanged,
                       self.margin.valueChanged, self.font_box.currentIndexChanged, self.color.colorChanged,
                       self.skip_first.toggled):
            signal.connect(self.update_preview)
        self.custom.textChanged.connect(lambda _: self.refresh())
        self.ranges.textChanged.connect(lambda _: self.refresh())
        self.finish_build()
        self.update_preview()

    def template(self) -> str:
        key = self.format.currentData()
        if key == "custom":
            return self.custom.text()
        if key == "page":
            return self.tr("Página {n}")
        if key == "page-of":
            return self.tr("Página {n} de {total}")
        return FORMAT_PRESETS[key]

    def options(self) -> PageNumberOptions:
        return PageNumberOptions(
            position=self.position.value(),
            template=self.template(),
            start=self.start.value(),
            font=self.font_box.currentData(),
            font_size=float(self.size_box.value()),
            color=_rgb(self.color.color()),
            margin=self.margin.value() * MM,
            pages=self.ranges.text(),
            skip_first=self.skip_first.isChecked(),
        )

    def is_ready(self):
        return self.source.is_ready() and self.ranges.is_valid() and "{n}" in self.template()

    def apply_to_preview(self, doc):
        options = self.options()
        options.pages = ""
        options.skip_first = False
        apply_page_numbers(doc, options)

    def job(self):
        source = self.require_source()
        options = self.options()
        try:
            options.validate()
        except Exception as exc:
            raise ValidationError(self.tr("El formato personalizado debe incluir {n}.")) from exc
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.numbering:add_page_numbers", (source, output, options), self.common_kwargs()


class CropDialog(_PreviewDialog):
    icon_name = "crop"
    output_suffix = "recortado"

    def title(self):
        return self.tr("Recortar márgenes")

    def description(self):
        return self.tr("Elimina los márgenes en blanco automáticamente o indica cuánto recortar de cada lado.")

    def action_text(self):
        return self.tr("Recortar")

    def build(self):
        self.add_source()
        self.begin_columns()
        self.mode = SegmentedControl([("auto", self.tr("Automático")), ("manual", self.tr("Manual"))])
        self.add_field(self.tr("Modo"), self.mode)
        self.mode_stack = AutoStack()
        auto_box = QWidget()
        auto_layout = QVBoxLayout(auto_box)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        self.padding = QDoubleSpinBox()
        self.padding.setRange(0, 50)
        self.padding.setValue(4)
        self.padding.setSuffix(" mm")
        auto_layout.addWidget(self.field_block(self.tr("Margen a conservar alrededor del contenido"), self.padding))
        manual_box = QWidget()
        grid = QGridLayout(manual_box)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(12)
        self.margins = {}
        for index, (key, name) in enumerate((("top", self.tr("Superior")), ("bottom", self.tr("Inferior")),
                                             ("left", self.tr("Izquierdo")), ("right", self.tr("Derecho")))):
            spin = QDoubleSpinBox()
            spin.setRange(0, 500)
            spin.setValue(10)
            spin.setSuffix(" mm")
            spin.valueChanged.connect(self.update_preview)
            self.margins[key] = spin
            grid.addWidget(self.field_block(name, spin), index // 2, index % 2)
        self.mode_stack.addWidget(auto_box)
        self.mode_stack.addWidget(manual_box)
        self.form.addWidget(self.mode_stack)
        self.mode.changed.connect(lambda key: (self.mode_stack.setCurrentIndex(0 if key == "auto" else 1), self.update_preview()))
        self.ranges = PageRangeEdit(allow_empty=True)
        self.ranges.setText(self.preset.get("pages", ""))
        self.ranges.textChanged.connect(lambda _: self.refresh())
        self.add_field(self.tr("Páginas"), self.ranges, self.tr("Déjalo vacío para todas."))
        self.end_columns()
        self.add_output()
        self.padding.valueChanged.connect(self.update_preview)
        self.finish_build()
        self.update_preview()

    def _margins(self) -> Margins:
        return Margins(
            top=self.margins["top"].value() * MM,
            right=self.margins["right"].value() * MM,
            bottom=self.margins["bottom"].value() * MM,
            left=self.margins["left"].value() * MM,
        )

    def is_ready(self):
        if not self.source.is_ready() or not self.ranges.is_valid():
            return False
        return self.mode.value() == "auto" or not self._margins().is_zero()

    def apply_to_preview(self, doc):
        if self.mode.value() == "auto":
            auto_crop_page(doc[0], self.padding.value() * MM)
        else:
            crop_page(doc[0], self._margins())

    def job(self):
        source = self.require_source()
        output = self.require_output(self.output, [self.source.original_path()])
        auto = self.mode.value() == "auto"
        return "blackpdf.core.geometry:crop_pdf", (source, output, None if auto else self._margins()), {
            "auto": auto,
            "padding": self.padding.value() * MM,
            "pages": self.ranges.text(),
            **self.common_kwargs(),
        }


class ResizeDialog(_PreviewDialog):
    icon_name = "scaling"
    output_suffix = "redimensionado"

    def title(self):
        return self.tr("Cambiar tamaño de página")

    def description(self):
        return self.tr("Ajusta todas las páginas a A4, Carta u otro tamaño, escalando el contenido sin deformarlo.")

    def action_text(self):
        return self.tr("Cambiar tamaño")

    def build(self):
        self.add_source()
        self.begin_columns()
        self.paper = QComboBox()
        for key, name in (("A4", "A4 (210 x 297 mm)"), ("letter", self.tr("Carta (216 x 279 mm)")),
                          ("A3", "A3 (297 x 420 mm)"), ("A5", "A5 (148 x 210 mm)"), ("legal", "Legal (216 x 356 mm)")):
            self.paper.addItem(name, key)
        self.add_field(self.tr("Tamaño de papel"), self.paper)
        self.orientation = SegmentedControl(
            [("auto", self.tr("Según la página")), ("portrait", self.tr("Vertical")), ("landscape", self.tr("Horizontal"))]
        )
        self.add_field(self.tr("Orientación"), self.orientation)
        self.form.addWidget(label(self.tr(
            "Las anotaciones se incorporan al contenido de la página para conservarlas."), "caption", wrap=True))
        self.end_columns()
        self.add_output()
        self.paper.currentIndexChanged.connect(self.update_preview)
        self.orientation.changed.connect(self.update_preview)
        self.finish_build()
        self.update_preview()

    def render_preview(self):
        doc = preview_page(self.source.preview_document())
        if doc is None:
            return None
        resized = resize_document(doc, self.paper.currentData(), self.orientation.value())
        page = resized[0]
        scale = self.preview.render_scale(page.rect)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    def job(self):
        source = self.require_source()
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.geometry:resize_pdf", (source, output, self.paper.currentData(), self.orientation.value()), \
            self.common_kwargs()


class FlattenDialog(_SingleSourceDialog):
    icon_name = "layers"
    output_suffix = "aplanado"

    def title(self):
        return self.tr("Aplanar PDF")

    def description(self):
        return self.tr(
            "Convierte anotaciones y campos de formulario en contenido fijo: se ven igual en cualquier "
            "programa y ya no se pueden modificar."
        )

    def action_text(self):
        return self.tr("Aplanar")

    def build(self):
        self.add_source()
        self.annotations = QCheckBox(self.tr("Anotaciones (dibujos, resaltados, notas, firmas)"))
        self.annotations.setChecked(True)
        self.forms = QCheckBox(self.tr("Campos de formulario"))
        self.forms.setChecked(True)
        self.form.addWidget(self.annotations)
        self.form.addWidget(self.forms)
        self.annotations.toggled.connect(lambda _: self.refresh())
        self.forms.toggled.connect(lambda _: self.refresh())
        self.add_output()
        self.finish_build()

    def is_ready(self):
        return self.source.is_ready() and (self.annotations.isChecked() or self.forms.isChecked())

    def job(self):
        source = self.require_source()
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.forms:flatten_pdf", (source, output), {
            "annotations": self.annotations.isChecked(),
            "forms": self.forms.isChecked(),
            **self.common_kwargs(),
        }
