"""Rellenar formularios del documento abierto (con deshacer)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.forms import EDITABLE_KINDS, FieldKey, fill_fields, flatten_document, list_fields
from ..document import PdfDocument
from ..errors import show_error
from ..widgets import Button, label
from .base import IconTile


class FormFillDialog(QDialog):
    def __init__(self, document: PdfDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self.setWindowTitle(self.tr("Rellenar formulario"))
        self.setModal(True)
        self.setMinimumSize(560, 480)
        self._editors: dict[FieldKey, tuple[str, QWidget]] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(IconTile("text-cursor-input"), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.addWidget(label(self.tr("Rellenar formulario"), "title"))
        texts.addWidget(label(self.tr("Completa los campos y aplica los cambios. Puedes deshacerlos con Ctrl+Z."),
                              "muted", wrap=True))
        header.addLayout(texts, 1)
        root.addLayout(header)

        fields = [f for f in list_fields(document.doc) if f.kind in EDITABLE_KINDS]
        if not fields:
            root.addWidget(label(self.tr("Este documento no tiene campos de formulario que se puedan rellenar."),
                                 "muted", wrap=True), 1)
        else:
            scroll = QScrollArea(self)
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            body = QWidget()
            form = QFormLayout(body)
            form.setVerticalSpacing(10)
            form.setHorizontalSpacing(16)
            form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            current_page = -1
            for field in fields:
                if field.key.page != current_page:
                    current_page = field.key.page
                    section = label(self.tr("Página {0}").format(current_page + 1), "section")
                    form.addRow(section)
                editor = self._editor_for(field)
                editor.setEnabled(not field.read_only)
                name = field.label or field.name or self.tr("Campo sin nombre")
                form.addRow(label(name, "body", wrap=True), editor)
                self._editors[field.key] = (field.kind, editor)
            scroll.setWidget(body)
            root.addWidget(scroll, 1)

        self.flatten = QCheckBox(self.tr("Aplanar al aplicar (los campos dejarán de ser editables)"))
        self.flatten.setVisible(bool(fields))
        root.addWidget(self.flatten)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = Button(self.tr("Cancelar") if fields else self.tr("Cerrar"))
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        if fields:
            apply = Button(self.tr("Aplicar cambios"), "primary", "check")
            apply.clicked.connect(self._apply)
            buttons.addWidget(apply)
        root.addLayout(buttons)

    def _editor_for(self, field) -> QWidget:
        if field.kind in ("checkbox", "radio"):
            box = QCheckBox()
            box.setChecked(bool(field.value))
            return box
        if field.kind in ("combobox", "listbox"):
            combo = QComboBox()
            combo.setEditable(field.kind == "combobox")
            combo.addItems(field.options)
            combo.setCurrentText(str(field.value))
            return combo
        if field.multiline:
            edit = QPlainTextEdit(str(field.value))
            edit.setFixedHeight(72)
            return edit
        edit = QLineEdit(str(field.value))
        if field.max_length:
            edit.setMaxLength(field.max_length)
        return edit

    def _values(self) -> dict:
        values = {}
        for key, (_kind, editor) in self._editors.items():
            if isinstance(editor, QCheckBox):
                values[key] = editor.isChecked()
            elif isinstance(editor, QComboBox):
                values[key] = editor.currentText()
            elif isinstance(editor, QPlainTextEdit):
                values[key] = editor.toPlainText()
            else:
                values[key] = editor.text()
        return values

    def _apply(self) -> None:
        values = self._values()
        flatten = self.flatten.isChecked()

        def operation(doc):
            fill_fields(doc, values)
            if flatten:
                flatten_document(doc)

        try:
            self.document.edit(self.tr("Rellenar formulario"), operation, None if flatten else sorted({k.page for k in values}))
        except Exception as exc:
            show_error(self, exc, "Formulario")
            return
        self.accept()
