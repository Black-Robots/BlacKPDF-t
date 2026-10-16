"""Proteger con contraseña y desbloquear."""

from __future__ import annotations

from PySide6.QtWidgets import QCheckBox, QGridLayout, QHBoxLayout, QLineEdit, QProgressBar, QWidget

from ...core.security import Permissions
from ..widgets import IconButton, label
from .base import ValidationError
from .organize import _SingleSourceDialog


def password_strength(password: str) -> int:
    """0-4: muy débil ... muy fuerte."""
    if not password:
        return 0
    score = 0
    if len(password) >= 8:
        score += 1
    if len(password) >= 12:
        score += 1
    classes = sum((
        any(c.islower() for c in password),
        any(c.isupper() for c in password),
        any(c.isdigit() for c in password),
        any(not c.isalnum() for c in password),
    ))
    if classes >= 2:
        score += 1
    if classes >= 3 and len(password) >= 10:
        score += 1
    return min(score, 4)


class PasswordField(QWidget):
    def __init__(self, placeholder: str, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.edit = QLineEdit(self)
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(placeholder)
        layout.addWidget(self.edit, 1)
        self.reveal = IconButton("eye", self.tr("Mostrar"), self, checkable=True)
        self.reveal.toggled.connect(self._toggle)
        layout.addWidget(self.reveal)

    def _toggle(self, on: bool) -> None:
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password)
        self.reveal.set_icon_name("eye-off" if on else "eye")

    def text(self) -> str:
        return self.edit.text()


class ProtectDialog(_SingleSourceDialog):
    icon_name = "lock"
    output_suffix = "protegido"

    def title(self):
        return self.tr("Proteger PDF")

    def description(self):
        return self.tr("Cifra el documento con AES de 256 bits: solo podrá abrirlo quien conozca la contraseña.")

    def action_text(self):
        return self.tr("Proteger")

    def build(self):
        self.add_source()
        self.password = PasswordField(self.tr("Contraseña"))
        self.confirm = PasswordField(self.tr("Repite la contraseña"))
        self.add_row(self.field_block(self.tr("Contraseña de apertura"), self.password),
                     self.field_block(self.tr("Confirmación"), self.confirm))
        strength_row = QWidget()
        strength_layout = QHBoxLayout(strength_row)
        strength_layout.setContentsMargins(0, 0, 0, 0)
        self.meter = QProgressBar()
        self.meter.setRange(0, 4)
        self.meter.setValue(0)
        self.meter.setTextVisible(False)
        self.meter.setFixedWidth(160)
        self.strength = label("", "caption")
        strength_layout.addWidget(self.meter)
        strength_layout.addWidget(self.strength, 1)
        self.form.addWidget(strength_row)
        self.meter.setVisible(False)

        self.restrict = QCheckBox(self.tr("Restringir lo que se puede hacer con el documento"))
        self.form.addWidget(self.restrict)
        self.permissions_box = QWidget()
        grid = QGridLayout(self.permissions_box)
        grid.setContentsMargins(24, 0, 0, 0)
        grid.setVerticalSpacing(8)
        self.allow = {}
        for index, (key, text) in enumerate((
            ("print", self.tr("Permitir imprimir")),
            ("copy", self.tr("Permitir copiar texto e imágenes")),
            ("modify", self.tr("Permitir modificar")),
            ("annotate", self.tr("Permitir anotar")),
            ("forms", self.tr("Permitir rellenar formularios")),
            ("assemble", self.tr("Permitir reorganizar páginas")),
        )):
            box = QCheckBox(text)
            box.setChecked(key in ("print", "forms"))
            self.allow[key] = box
            grid.addWidget(box, index // 2, index % 2)
        self.owner = PasswordField(self.tr("Contraseña de propietario (opcional)"))
        grid.addWidget(self.owner, 3, 0, 1, 2)
        grid.addWidget(label(self.tr(
            "Con la contraseña de propietario se pueden quitar las restricciones más adelante. "
            "Si la dejas vacía se genera una aleatoria."), "caption", wrap=True), 4, 0, 1, 2)
        self.permissions_box.setVisible(False)
        self.form.addWidget(self.permissions_box)
        self.restrict.toggled.connect(self.permissions_box.setVisible)
        self.add_output()
        self.password.edit.textChanged.connect(self._password_changed)
        self.confirm.edit.textChanged.connect(lambda _: self.refresh())
        self.finish_build()

    def _password_changed(self, text: str) -> None:
        score = password_strength(text)
        self.meter.setValue(score)
        names = [self.tr("Muy débil"), self.tr("Débil"), self.tr("Aceptable"), self.tr("Fuerte"), self.tr("Muy fuerte")]
        self.strength.setText(names[score] if text else "")
        self.meter.setVisible(bool(text))
        self.refresh()

    def is_ready(self):
        return self.source.is_ready() and bool(self.password.text()) and self.password.text() == self.confirm.text()

    def job(self):
        source = self.require_source()
        if not self.password.text():
            raise ValidationError(self.tr("Escribe una contraseña."))
        if self.password.text() != self.confirm.text():
            raise ValidationError(self.tr("Las contraseñas no coinciden."))
        output = self.require_output(self.output, [self.source.original_path()])
        permissions = Permissions()
        owner = ""
        if self.restrict.isChecked():
            permissions = Permissions(**{key: box.isChecked() for key, box in self.allow.items()})
            owner = self.owner.text()
        return "blackpdf.core.security:protect_pdf", (source, output, self.password.text(), owner, permissions), \
            self.common_kwargs()


class UnlockDialog(_SingleSourceDialog):
    icon_name = "lock-open"
    output_suffix = "desbloqueado"

    def title(self):
        return self.tr("Desbloquear PDF")

    def description(self):
        return self.tr(
            "Guarda una copia sin contraseña ni restricciones. Necesitas conocer la contraseña si el PDF la pide para abrirse."
        )

    def action_text(self):
        return self.tr("Desbloquear")

    def build(self):
        self.add_source()
        self.form.addWidget(label(self.tr(
            "Usa esta herramienta solo con documentos que te pertenezcan o que tengas permiso para modificar."),
            "caption", wrap=True))
        self.add_output()
        self.finish_build()

    def job(self):
        source = self.require_source()
        output = self.require_output(self.output, [self.source.original_path()])
        return "blackpdf.core.security:unlock_pdf", (source, output, self.source.source_password() or ""), {}

