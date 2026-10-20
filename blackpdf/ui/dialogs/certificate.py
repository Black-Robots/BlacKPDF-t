"""Firma digital con certificado (PAdES) y detalle de las firmas de un documento."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pymupdf
from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication, QRectF, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...core.signatures import (
    DEFAULT_TSA,
    CertificateError,
    CertificatePasswordError,
    SignatureStatus,
    SignOptions,
    StampText,
    build_appearance,
    load_certificate,
    stamp_rect,
)
from .. import formatting, icons
from ..settings import settings
from ..widgets import Button, PathPicker, SectionLabel, SegmentedControl, label
from .base import ValidationError
from .edit import PositionPicker, _PreviewDialog
from .security import PasswordField
from .signature import SignatureDialog


def _tr(text: str) -> str:
    return QCoreApplication.translate("Signatures", text)


def certificate_filter() -> str:
    return _tr("Certificados digitales (*.p12 *.pfx)")


REASONS = (
    QT_TRANSLATE_NOOP("Signatures", "Soy el autor de este documento"),
    QT_TRANSLATE_NOOP("Signatures", "He revisado este documento"),
    QT_TRANSLATE_NOOP("Signatures", "Apruebo este documento"),
    QT_TRANSLATE_NOOP("Signatures", "Conforme con el contenido"),
)


class CertificateSignDialog(_PreviewDialog):
    icon_name = "shield-check"
    output_suffix = "firmado"

    def title(self):
        return self.tr("Firma digital")

    def description(self):
        return self.tr(
            "Firma con tu certificado (FNMT, DNIe, certificado de empresa). Certifica quién firma y "
            "garantiza que el documento no se modifica después."
        )

    def action_text(self):
        return self.tr("Firmar")

    def preview_caption(self):
        return self.tr("Vista previa de la página firmada")

    def build(self):
        self._info = None
        self.add_source()
        self.begin_columns()

        self.certificate = PathPicker("open", certificate_filter(), self,
                                      placeholder=self.tr("Archivo .pfx o .p12"))
        self.certificate.set_path(str(settings().value("signing/certificate", "") or ""))
        self.add_field(self.tr("Certificado"), self.certificate, self.tr(
            "Si lo tienes instalado en Windows o en el navegador, expórtalo como archivo .pfx con su clave privada."))
        self.cert_password = PasswordField(self.tr("Contraseña del certificado"))
        self.add_field(self.tr("Contraseña"), self.cert_password)
        self.cert_card = QFrame()
        self.cert_card.setProperty("role", "panel")
        card_layout = QHBoxLayout(self.cert_card)
        card_layout.setContentsMargins(12, 10, 12, 10)
        card_layout.setSpacing(10)
        self.cert_icon = QLabel()
        card_layout.addWidget(self.cert_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.cert_text = label("", "body", wrap=True)
        card_layout.addWidget(self.cert_text, 1)
        self.cert_card.setVisible(False)
        self.form.addWidget(self.cert_card)
        self._check_timer = QTimer(self)
        self._check_timer.setSingleShot(True)
        self._check_timer.setInterval(350)
        self._check_timer.timeout.connect(self._check_certificate)
        self.certificate.changed.connect(lambda _: self._check_timer.start())
        self.cert_password.edit.textChanged.connect(lambda _: self._check_timer.start())

        self.visible = SegmentedControl([("visible", self.tr("Sello visible")), ("invisible", self.tr("Invisible"))])
        self.add_field(self.tr("Apariencia"), self.visible)
        self.visible_box = QWidget()
        visible_layout = QVBoxLayout(self.visible_box)
        visible_layout.setContentsMargins(0, 0, 0, 0)
        visible_layout.setSpacing(12)
        place = QWidget()
        place_layout = QHBoxLayout(place)
        place_layout.setContentsMargins(0, 0, 0, 0)
        place_layout.setSpacing(16)
        self.position = PositionPicker([["top-left", "top-right"], ["", ""], ["bottom-left", "bottom-right"]],
                                       "bottom-right")
        place_layout.addWidget(self.position)
        column = QVBoxLayout()
        self.page_choice = QComboBox()
        self.page_choice.addItem(self.tr("Última página"), -1)
        self.page_choice.addItem(self.tr("Primera página"), 0)
        column.addWidget(self.page_choice)
        self.handwritten = QCheckBox(self.tr("Incluir mi firma manuscrita"))
        self.handwritten.setChecked(bool(settings().signatures()))
        column.addWidget(self.handwritten)
        draw = Button(self.tr("Dibujar o cambiar firma..."), "ghost", "signature")
        draw.clicked.connect(self._draw_signature)
        column.addWidget(draw, 0, Qt.AlignmentFlag.AlignLeft)
        column.addStretch(1)
        place_layout.addLayout(column, 1)
        visible_layout.addWidget(self.field_block(self.tr("Posición"), place))
        self.form.addWidget(self.visible_box)
        self.visible.changed.connect(lambda key: (self.visible_box.setVisible(key == "visible"), self.update_preview()))

        self.reason = QComboBox()
        self.reason.setEditable(True)
        for reason in REASONS:
            self.reason.addItem(_tr(reason))
        self.reason.setCurrentText(str(settings().value("signing/reason", "") or _tr(REASONS[0])))
        self.location = QLineEdit(str(settings().value("signing/location", "") or ""))
        self.location.setPlaceholderText(self.tr("Ciudad (opcional)"))
        self.add_row(self.field_block(self.tr("Motivo"), self.reason), self.field_block(self.tr("Lugar"), self.location))
        self.timestamp = QCheckBox(self.tr("Añadir sello de tiempo de un servidor de confianza (requiere conexión)"))
        self.form.addWidget(self.timestamp)
        self.tsa = QLineEdit(str(settings().value("signing/tsa", "") or DEFAULT_TSA))
        self.tsa.setVisible(False)
        self.timestamp.toggled.connect(self.tsa.setVisible)
        self.form.addWidget(self.tsa)
        self.end_columns()
        self.add_output()

        for signal in (self.position.changed, self.page_choice.currentIndexChanged, self.handwritten.toggled,
                       self.reason.currentTextChanged, self.location.textChanged):
            signal.connect(self.update_preview)
        self.finish_build()
        if self.context is not None:
            self.page_choice.addItem(self.tr("Página actual"), -2)
        self._check_timer.start()
        self.update_preview()

    # ---------- certificado ----------
    def _check_certificate(self) -> None:
        path = self.certificate.path()
        password = self.cert_password.text()
        self._info = None
        if not path or not Path(path).is_file():
            self.cert_card.setVisible(False)
            self.refresh()
            return
        if not password:
            self._show_card("lock", icons.ROLE_MUTED, self.tr("Escribe la contraseña del certificado."))
            self.refresh()
            return
        try:
            info = load_certificate(path, password)
        except CertificatePasswordError:
            self._show_card("shield-x", icons.ROLE_DANGER, self.tr("La contraseña no es correcta o el archivo no es un certificado."))
            self.refresh()
            return
        except CertificateError:
            self._show_card("shield-x", icons.ROLE_DANGER, self.tr("El archivo no contiene un certificado con clave privada."))
            self.refresh()
            return
        except Exception:
            self._show_card("shield-x", icons.ROLE_DANGER, self.tr("No se ha podido leer el certificado."))
            self.refresh()
            return
        self._info = info
        valid_to = formatting.date(info.valid_to.astimezone())
        details = f"<b>{info.name}</b><br>" + self.tr("Emitido por {0}").format(info.issuer)
        if info.expired:
            details += "<br>" + self.tr("Caducó el {0}: no se puede usar para firmar.").format(valid_to)
            self._show_card("shield-alert", icons.ROLE_DANGER, details)
        else:
            details += "<br>" + self.tr("Válido hasta el {0}").format(valid_to)
            self._show_card("shield-check", icons.ROLE_SUCCESS, details)
        self.update_preview()
        self.refresh()

    def _show_card(self, icon_name: str, role: str, text: str) -> None:
        self.cert_icon.setPixmap(icons.pixmap(icon_name, 22, role, self.devicePixelRatioF()))
        self.cert_text.setText(text)
        self.cert_card.setVisible(True)

    def _draw_signature(self) -> None:
        dialog = SignatureDialog(self)
        if dialog.exec() == SignatureDialog.DialogCode.Accepted and dialog.strokes():
            self._strokes = dialog.strokes()
            self.handwritten.setChecked(True)
            self.update_preview()

    def _handwritten(self) -> list:
        if not self.handwritten.isChecked():
            return []
        strokes = getattr(self, "_strokes", None)
        if strokes:
            return strokes
        saved = settings().signatures()
        return saved[0] if saved else []

    # ---------- página y vista previa ----------
    def _page_index(self) -> int:
        count = self.source.page_count or 1
        choice = self.page_choice.currentData()
        if choice == -2 and self.context is not None:
            parent = self.parent()
            tab = parent.current_tab() if hasattr(parent, "current_tab") else None
            return tab.viewer.current_page if tab is not None else count - 1
        return count - 1 if choice == -1 else 0

    def render_preview(self):
        source = self.source.preview_document()
        if source is None:
            return None
        index = min(self._page_index(), source.page_count - 1)
        doc = pymupdf.open()
        doc.insert_pdf(source, from_page=index, to_page=index, annots=True)
        page = doc[0]
        if self.visible.value() == "visible":
            rect = stamp_rect(page, self.position.value())
            signer = self._info.name if self._info is not None else self.tr("Nombre del titular")
            stamp = pymupdf.open("pdf", build_appearance(
                rect.width, rect.height, signer, dt.datetime.now(dt.timezone.utc),
                self.reason.currentText().strip(), self.location.text().strip(), self._handwritten(),
                stamp_text(),
            ))
            page.show_pdf_page(rect * page.derotation_matrix, stamp, 0, rotate=page.rotation)
            page.draw_rect(rect * page.derotation_matrix, color=(0.898, 0.282, 0.302), width=0.8, dashes="[3 2] 0")
        scale = self.preview.render_scale(page.rect)
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)

    # ---------- ejecución ----------
    def is_ready(self):
        return self.source.is_ready() and self._info is not None and not self._info.expired

    def job(self):
        source = self.require_source()
        if self._info is None:
            raise ValidationError(self.tr("Elige tu certificado y escribe su contraseña."))
        output = self.require_output(self.output, [self.source.original_path()])
        reason = self.reason.currentText().strip()
        location = self.location.text().strip()
        tsa = self.tsa.text().strip() if self.timestamp.isChecked() else ""
        settings().set_value("signing/certificate", self.certificate.path())
        settings().set_value("signing/reason", reason)
        settings().set_value("signing/location", location)
        if tsa:
            settings().set_value("signing/tsa", tsa)
        options = SignOptions(
            visible=self.visible.value() == "visible",
            page=self._page_index(),
            position=self.position.value(),
            reason=reason,
            location=location,
            handwritten=self._handwritten(),
            timestamp_url=tsa,
            text=stamp_text(),
        )
        return "blackpdf.core.signatures:sign_pdf", (
            source, output, self.certificate.path(), self.cert_password.text(), options
        ), self.common_kwargs()

    def describe_result(self, result):
        return self.tr("Documento firmado"), self.tr(
            "Se ha guardado «{0}». Cualquier cambio posterior en ese archivo invalidará la firma."
        ).format(Path(result).name), [Path(result)]


def stamp_text() -> StampText:
    """Textos del sello visible en el idioma de la interfaz."""
    from ..i18n import current

    return StampText(
        signed_by=_tr("Firmado digitalmente por"),
        date=_tr("Fecha: {0}"),
        reason=_tr("Motivo: {0}"),
        location=_tr("Lugar: {0}"),
        # Fecha sin ambigüedad en inglés (el orden día/mes cambia según el país).
        date_format="%Y-%m-%d %H:%M" if current() == "en" else "%d/%m/%Y %H:%M",
    )


SUBJECT_LABELS = {
    "common_name": QT_TRANSLATE_NOOP("Signatures", "Nombre"),
    "given_name": QT_TRANSLATE_NOOP("Signatures", "Nombre de pila"),
    "surname": QT_TRANSLATE_NOOP("Signatures", "Apellidos"),
    "serial_number": QT_TRANSLATE_NOOP("Signatures", "Número de serie"),
    "organization_name": QT_TRANSLATE_NOOP("Signatures", "Organización"),
    "organizational_unit_name": QT_TRANSLATE_NOOP("Signatures", "Unidad"),
    "organization_identifier": QT_TRANSLATE_NOOP("Signatures", "Identificador de la organización"),
    "title": QT_TRANSLATE_NOOP("Signatures", "Cargo"),
    "email_address": QT_TRANSLATE_NOOP("Signatures", "Correo"),
    "locality_name": QT_TRANSLATE_NOOP("Signatures", "Localidad"),
    "state_or_province_name": QT_TRANSLATE_NOOP("Signatures", "Provincia"),
    "country_name": QT_TRANSLATE_NOOP("Signatures", "País"),
}


def subject_text(status: SignatureStatus) -> str:
    """Datos del titular con los nombres de campo traducidos."""
    if not status.subject:
        return status.signer_details
    return "  ·  ".join(f"{_tr(SUBJECT_LABELS.get(key, key))}: {value}" for key, value in status.subject)


# ======================================================================
# Detalle de firmas
# ======================================================================
def status_text(status: SignatureStatus) -> tuple[str, str]:
    """(icono, frase) que resume una firma."""
    who = status.signer or _tr("firmante desconocido")
    if status.level == "invalid":
        return "shield-x", _tr("La firma de {0} no es válida: el documento se ha modificado después de firmarse.").format(who)
    if not (status.covers_whole_file or status.changes_allowed):
        return "shield-alert", _tr("Firmado por {0}, pero se hicieron cambios después de la firma.").format(who)
    if not status.trusted:
        return "shield-alert", _tr(
            "Firmado por {0}. El documento no se ha modificado, pero el certificado no procede de una "
            "entidad de confianza reconocida.").format(who)
    return "shield-check", _tr("Firmado por {0}. La firma es válida y el documento no se ha modificado.").format(who)


ROLE_FOR_LEVEL = {"valid": icons.ROLE_SUCCESS, "warning": icons.ROLE_WARNING, "invalid": icons.ROLE_DANGER}


class SignaturesDialog(QDialog):
    goToSignature = Signal(int, QRectF)

    def __init__(self, statuses: list[SignatureStatus], parent=None):
        super().__init__(parent)
        self.setWindowTitle(self.tr("Firmas del documento"))
        self.setMinimumSize(560, 420)
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(14)
        root.addWidget(label(self.tr("Firmas del documento"), "title"))
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(0, 0, 4, 0)
        layout.setSpacing(12)
        for status in statuses:
            layout.addWidget(self._card(status))
        layout.addStretch(1)
        scroll.setWidget(body)
        root.addWidget(scroll, 1)
        root.addWidget(label(self.tr(
            "La comprobación se hace sin conexión: no se consulta si el certificado ha sido revocado."),
            "caption", wrap=True))
        row = QHBoxLayout()
        row.addStretch(1)
        close = Button(self.tr("Cerrar"), "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        root.addLayout(row)

    def _card(self, status: SignatureStatus) -> QFrame:
        card = QFrame()
        card.setProperty("role", "panel")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(8)
        header = QHBoxLayout()
        icon_name, summary = status_text(status)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap(icon_name, 26, ROLE_FOR_LEVEL[status.level], self.devicePixelRatioF()))
        header.addWidget(glyph, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(label(summary, "body", wrap=True), 1)
        layout.addLayout(header)
        details = [
            (self.tr("Firmante"), subject_text(status) or status.signer or "-"),
            (self.tr("Emisor del certificado"), status.issuer or "-"),
            (self.tr("Fecha de la firma"),
             formatting.date_time(status.signed_at) if status.signed_at else status.signing_time or "-"),
        ]
        if status.reason:
            details.append((self.tr("Motivo"), status.reason))
        if status.location:
            details.append((self.tr("Lugar"), status.location))
        for key, value in details:
            row = QHBoxLayout()
            key_label = label(key, "muted")
            key_label.setFixedWidth(150)
            row.addWidget(key_label, 0, Qt.AlignmentFlag.AlignTop)
            value_label = label(value, "body", wrap=True)
            value_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            row.addWidget(value_label, 1)
            layout.addLayout(row)
        layout.addWidget(SectionLabel(self.tr("Comprobaciones")))
        checks = [
            (status.intact, self.tr("El contenido firmado no ha cambiado")),
            (status.valid, self.tr("La firma criptográfica es correcta")),
            (status.trusted, self.tr("Certificado de una entidad de confianza")),
            (status.covers_whole_file or status.changes_allowed, self.tr("Sin cambios no permitidos después de firmar")),
        ]
        for ok, text in checks:
            row = QHBoxLayout()
            mark = QLabel()
            mark.setPixmap(icons.pixmap("circle-check" if ok else "circle-x", 16,
                                        icons.ROLE_SUCCESS if ok else icons.ROLE_DANGER, self.devicePixelRatioF()))
            row.addWidget(mark)
            row.addWidget(label(text, "body"), 1)
            layout.addLayout(row)
        if status.rect is not None and status.page >= 0:
            go = Button(self.tr("Ir a la firma"), "ghost", "arrow-right")
            go.clicked.connect(lambda: (self.goToSignature.emit(status.page, QRectF(
                status.rect[0], status.rect[1], status.rect[2] - status.rect[0], status.rect[3] - status.rect[1])),
                self.accept()))
            layout.addWidget(go, 0, Qt.AlignmentFlag.AlignLeft)
        return card
