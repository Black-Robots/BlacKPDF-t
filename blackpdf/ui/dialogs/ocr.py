"""Reconocer texto (OCR) en documentos escaneados."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QComboBox

from ...core.ocr import LANGUAGE_NAMES, available_languages
from ..widgets import PageRangeEdit, label
from .base import ValidationError
from .organize import _SingleSourceDialog


class OcrDialog(_SingleSourceDialog):
    icon_name = "scan-text"
    output_suffix = "ocr"

    def title(self):
        return self.tr("Reconocer texto (OCR)")

    def description(self):
        return self.tr(
            "Convierte las páginas escaneadas en texto que se puede buscar, seleccionar y copiar. "
            "Todo se procesa en tu equipo."
        )

    def action_text(self):
        return self.tr("Reconocer texto")

    def build(self):
        self.add_source()
        self.languages = available_languages()
        self.language = QComboBox()
        codes = sorted(self.languages, key=lambda c: (c != "spa", c != "eng", LANGUAGE_NAMES.get(c, c)))
        for code in codes:
            self.language.addItem(LANGUAGE_NAMES.get(code, code), [code])
        if "spa" in self.languages and "eng" in self.languages:
            self.language.insertItem(1, self.tr("Español + Inglés"), ["spa", "eng"])
        self.language.setCurrentIndex(0)
        self.quality = QComboBox()
        self.quality.addItem(self.tr("Rápida (200 ppp)"), 200)
        self.quality.addItem(self.tr("Precisa (300 ppp)"), 300)
        self.quality.setCurrentIndex(1)
        self.add_row(self.field_block(self.tr("Idioma del documento"), self.language),
                     self.field_block(self.tr("Calidad"), self.quality))
        self.skip = QCheckBox(self.tr("Omitir las páginas que ya tienen texto"))
        self.skip.setChecked(True)
        self.form.addWidget(self.skip)
        self.ranges = PageRangeEdit(allow_empty=True)
        self.ranges.textChanged.connect(lambda _: self.refresh())
        self.add_field(self.tr("Páginas"), self.ranges, self.tr("Déjalo vacío para todas."))
        self.form.addWidget(label(self.tr(
            "Las páginas reconocidas se guardan como imagen con el texto encima, igual que hace un escáner con OCR."),
            "caption", wrap=True))
        self.add_output()
        self.finish_build()

    def on_source_changed(self):
        self.ranges.set_page_count(self.source.page_count)

    def is_ready(self):
        return self.source.is_ready() and self.ranges.is_valid() and self.language.count() > 0

    def job(self):
        source = self.require_source()
        if self.language.count() == 0:
            raise ValidationError(self.tr("No hay idiomas de reconocimiento instalados."))
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.ocr:ocr_pdf", (source, output), {
            "languages": self.language.currentData(),
            "dpi": int(self.quality.currentData()),
            "pages": self.ranges.text(),
            "skip_text_pages": self.skip.isChecked(),
            **self.common_kwargs(),
        }
