"""Etiquetas de página: reglas de numeración («i, ii, iii», «A-101»...) con vista previa."""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...core.page_labels import LabelRule, labels_from_rules
from ..widgets import Button, IconButton, SectionLabel, label
from .base import IconTile


def _tr(text: str) -> str:
    return QCoreApplication.translate("PageLabelsDialog", text)


def style_names() -> list[tuple[str, str]]:
    return [("D", _tr("1, 2, 3")), ("r", _tr("i, ii, iii")), ("R", _tr("I, II, III")),
            ("a", _tr("a, b, c")), ("A", _tr("A, B, C")), ("", _tr("Sin número (solo prefijo)"))]


class PageLabelsDialog(QDialog):
    def __init__(self, rules: list[LabelRule], page_count: int, parent=None, start_at: int | None = None):
        super().__init__(parent)
        self.page_count = page_count
        self.rows: list[dict] = []
        self.setWindowTitle(self.tr("Etiquetas de página"))
        self.setMinimumWidth(640)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(IconTile("hash"), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.addWidget(label(self.tr("Etiquetas de página"), "title"))
        texts.addWidget(label(self.tr(
            "Numera como un libro (prólogo en romanos) o un juego de planos (A-101, A-102...). Cada regla "
            "empieza en una página y sigue hasta la siguiente regla."), "muted", wrap=True))
        header.addLayout(texts, 1)
        root.addLayout(header)

        self.grid_holder = QWidget(self)
        self.grid = QGridLayout(self.grid_holder)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(10)
        self.grid.setVerticalSpacing(8)
        for column, text in enumerate((self.tr("Desde la página"), self.tr("Estilo"), self.tr("Prefijo"),
                                       self.tr("Empieza en"))):
            self.grid.addWidget(SectionLabel(text), 0, column)
        root.addWidget(self.grid_holder)
        add = Button(self.tr("Añadir regla"), "ghost", "plus")
        add.clicked.connect(lambda: self.add_rule(LabelRule(self._next_free_page())))
        root.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)

        root.addWidget(SectionLabel(self.tr("Vista previa")))
        self.preview = label("", "body", wrap=True)
        root.addWidget(self.preview)

        line = QFrame(self)
        line.setFrameShape(QFrame.Shape.HLine)
        root.addWidget(line)
        row = QHBoxLayout()
        self.clear_button = Button(self.tr("Quitar todas"), "ghost")
        self.clear_button.clicked.connect(self._clear)
        row.addWidget(self.clear_button)
        row.addStretch(1)
        cancel = Button(self.tr("Cancelar"), "ghost")
        cancel.clicked.connect(self.reject)
        row.addWidget(cancel)
        self.ok = Button(self.tr("Guardar"), "primary")
        self.ok.clicked.connect(self.accept)
        row.addWidget(self.ok)
        root.addLayout(row)

        for rule in rules:
            self.add_rule(rule)
        if start_at is not None and all(r.start != start_at for r in rules):
            self.add_rule(LabelRule(start_at))
        if not self.rows:
            self.add_rule(LabelRule(0))
        self._refresh()

    def _next_free_page(self) -> int:
        used = {row["start"].value() - 1 for row in self.rows}
        return next((p for p in range(self.page_count) if p not in used), 0)

    def add_rule(self, rule: LabelRule) -> None:
        start = QSpinBox()
        start.setRange(1, max(1, self.page_count))
        start.setValue(rule.start + 1)
        style = QComboBox()
        for key, name in style_names():
            style.addItem(name, key)
        style.setCurrentIndex(max(0, style.findData(rule.style)))
        prefix = QLineEdit(rule.prefix)
        prefix.setPlaceholderText(self.tr("Por ejemplo, A-"))
        first = QSpinBox()
        first.setRange(1, 99999)
        first.setValue(rule.first)
        remove = IconButton("x", self.tr("Quitar esta regla"))
        row = {"start": start, "style": style, "prefix": prefix, "first": first, "remove": remove}
        remove.clicked.connect(lambda: self._remove(row))
        for widget in (start, first):
            widget.valueChanged.connect(self._refresh)
        style.currentIndexChanged.connect(self._refresh)
        prefix.textChanged.connect(self._refresh)
        line = len(self.rows) + 1
        for column, widget in enumerate((start, style, prefix, first, remove)):
            self.grid.addWidget(widget, line, column)
        self.rows.append(row)
        self._refresh()

    def _remove(self, row: dict) -> None:
        self.rows.remove(row)
        for widget in row.values():
            self.grid.removeWidget(widget)
            widget.deleteLater()
        self._refresh()

    def _clear(self) -> None:
        for row in list(self.rows):
            self._remove(row)

    def rules(self) -> list[LabelRule]:
        return [LabelRule(row["start"].value() - 1, row["style"].currentData(), row["prefix"].text().strip(),
                          row["first"].value()) for row in self.rows]

    def _refresh(self, *_args) -> None:
        rules = self.rules()
        starts = [rule.start for rule in rules]
        duplicated = len(starts) != len(set(starts))
        self.ok.setEnabled(not duplicated)
        if duplicated:
            self.preview.setText(self.tr("Hay dos reglas que empiezan en la misma página."))
            return
        if not rules:
            self.preview.setText(self.tr("Sin etiquetas: las páginas se numeran 1, 2, 3..."))
            return
        labels = labels_from_rules(rules, self.page_count)
        shown = labels if len(labels) <= 12 else labels[:8] + ["..."] + labels[-3:]
        self.preview.setText(", ".join(shown))
