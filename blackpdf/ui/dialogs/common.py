"""Diálogos pequeños de uso general: contraseña, confirmaciones y propiedades."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QCoreApplication, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
    QWidget,
)

from ...core.document import DocumentInfo
from .. import formatting, icons
from ..widgets import Button, IconButton, label


def _tr(text: str) -> str:
    return QCoreApplication.translate("Dialogs", text)


class PasswordDialog(QDialog):
    def __init__(self, file_name: str, retry: bool = False, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Documento protegido"))
        self.setModal(True)
        self.setMinimumWidth(400)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(12)
        header = QHBoxLayout()
        header.setSpacing(12)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("lock", 28, icons.ROLE_ACCENT, self.devicePixelRatioF()))
        header.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(label(self.tr("Introduce la contraseña"), "title"))
        texts.addWidget(label(self.tr("«{0}» está protegido.").format(file_name), "muted", wrap=True))
        header.addLayout(texts, 1)
        layout.addLayout(header)

        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(self.tr("Contraseña"))
        row.addWidget(self.edit, 1)
        self.reveal = IconButton("eye", self.tr("Mostrar contraseña"), checkable=True)
        self.reveal.toggled.connect(
            lambda on: self.edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        )
        row.addWidget(self.reveal)
        layout.addLayout(row)
        self.error = label(self.tr("La contraseña no es correcta."), "error")
        self.error.setVisible(retry)
        layout.addWidget(self.error)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = Button(self.tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        ok = Button(self.tr("Abrir"), "primary")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(ok)
        layout.addLayout(buttons)
        self.edit.setFocus()

    def password(self) -> str:
        return self.edit.text()


def ask_password(parent: QWidget | None, path: str, retry: bool = False) -> str | None:
    dialog = PasswordDialog(Path(path).name if path else "", retry, parent)
    if dialog.exec() == QDialog.DialogCode.Accepted and dialog.password():
        return dialog.password()
    return None


def confirm_discard(parent: QWidget | None, name: str) -> QMessageBox.StandardButton:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(_tr("Cambios sin guardar"))
    box.setText("<b>" + _tr("¿Guardar los cambios en «{0}»?").format(name) + "</b>")
    box.setInformativeText(_tr("Si no los guardas, se perderán."))
    box.setStandardButtons(
        QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel
    )
    box.button(QMessageBox.StandardButton.Save).setText(_tr("Guardar"))
    box.button(QMessageBox.StandardButton.Discard).setText(_tr("No guardar"))
    box.button(QMessageBox.StandardButton.Cancel).setText(_tr("Cancelar"))
    box.setDefaultButton(QMessageBox.StandardButton.Save)
    return QMessageBox.StandardButton(box.exec())


def _paper_name(width: float, height: float) -> str:
    names = {
        (595, 842): "A4", (842, 1191): "A3", (420, 595): "A5", (612, 792): "Carta", (612, 1008): "Legal",
    }
    key = (round(min(width, height)), round(max(width, height)))
    for (w, h), name in names.items():
        if abs(key[0] - w) <= 2 and abs(key[1] - h) <= 2:
            return name
    return ""


class PropertiesDialog(QDialog):
    def __init__(self, info: DocumentInfo, path: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Propiedades del documento"))
        self.setMinimumWidth(460)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 20)
        layout.setSpacing(16)
        layout.addWidget(label(Path(path).name if path else self.tr("Documento sin guardar"), "title"))
        form = QFormLayout()
        form.setHorizontalSpacing(24)
        form.setVerticalSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)
        width_mm = info.page_size[0] * 25.4 / 72
        height_mm = info.page_size[1] * 25.4 / 72
        paper = _paper_name(*info.page_size)
        size_text = f"{width_mm:.0f} x {height_mm:.0f} mm" + (f" ({paper})" if paper else "")
        file_size = ""
        if path and Path(path).exists():
            file_size = formatting.size(Path(path).stat().st_size)
        rows = [
            (self.tr("Ubicación"), str(Path(path).parent) if path else "-"),
            (self.tr("Tamaño"), file_size or "-"),
            (self.tr("Páginas"), str(info.page_count)),
            (self.tr("Tamaño de página"), size_text),
            (self.tr("Título"), info.title or "-"),
            (self.tr("Autor"), info.author or "-"),
            (self.tr("Asunto"), info.subject or "-"),
            (self.tr("Creado con"), info.creator or "-"),
            (self.tr("Productor PDF"), info.producer or "-"),
            (self.tr("Creación"), info.created or "-"),
            (self.tr("Modificación"), info.modified or "-"),
            (self.tr("Versión PDF"), info.pdf_version or "-"),
            (self.tr("Protegido"), self.tr("Sí") if info.encrypted else self.tr("No")),
            (self.tr("Formulario"), self.tr("Sí") if info.has_forms else self.tr("No")),
        ]
        for name, value in rows:
            key = label(name, "muted")
            text = label(value, wrap=True)
            text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(key, text)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(self.tr("Cerrar"))
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
