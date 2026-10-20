"""Marco común de los diálogos de herramientas.

Cada herramienta describe su formulario y el trabajo a lanzar; este módulo
se encarga del origen (documento abierto o archivo, con contraseña), del
destino ("Guardar como" por defecto, nunca el original), del progreso en
segundo plano, de la cancelación y del panel de resultado.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pymupdf
from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.document import needs_password, open_pdf
from ...core.errors import BlackPdfError, WrongPasswordError
from ...core.files import unique_path
from .. import icons, tasks, theme
from ..errors import show_crash, show_error, user_message
from ..platform import reveal_in_folder
from ..settings import settings
from ..widgets import Button, DropZone, PathPicker, label, pdf_filter
from .common import ask_password


@dataclass
class SourceContext:
    """Documento abierto en el editor que se ofrece como origen."""

    name: str
    path: str
    page_count: int
    modified: bool
    encrypted: bool
    snapshot: Callable[[], bytes]
    document: pymupdf.Document


class ValidationError(Exception):
    """Dato del formulario incorrecto; el mensaje se muestra junto al botón."""


class IconTile(QLabel):
    def __init__(self, icon_name: str, size: int = 44, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.tile = size
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        p = theme.current()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(p.qcolor("accent_soft"))
        painter.drawRoundedRect(self.rect(), theme.RADIUS + 2, theme.RADIUS + 2)
        glyph = icons.pixmap(self.icon_name, int(self.tile * 0.5), icons.ROLE_ACCENT, self.devicePixelRatioF())
        x = (self.width() - glyph.width() / glyph.devicePixelRatio()) / 2
        y = (self.height() - glyph.height() / glyph.devicePixelRatio()) / 2
        painter.drawPixmap(int(x), int(y), glyph)


class SourcePicker(QWidget):
    """Elige el PDF de entrada: el documento abierto o un archivo."""

    changed = Signal()

    def __init__(self, context: SourceContext | None, parent=None, title: str = ""):
        super().__init__(parent)
        self.context = context
        self.path = ""
        self.password: str | None = None
        self.page_count = 0
        self.name = ""
        self._preview_doc: pymupdf.Document | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.stack = QStackedWidget(self)
        self.stack.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self.drop = DropZone((".pdf",), self)
        self.drop.setFixedHeight(84)
        drop_layout = QHBoxLayout(self.drop)
        drop_layout.setContentsMargins(18, 12, 18, 12)
        glyph = QLabel()
        glyph.setPixmap(icons.pixmap("file-up", 22, icons.ROLE_MUTED, self.devicePixelRatioF()))
        drop_layout.addWidget(glyph)
        drop_texts = QVBoxLayout()
        drop_texts.setSpacing(2)
        drop_texts.addWidget(label(title or self.tr("Elige un PDF"), "cardtitle"))
        drop_texts.addWidget(label(self.tr("Arrastra el archivo aquí o haz clic para buscarlo"), "caption"))
        drop_layout.addLayout(drop_texts, 1)
        self.drop.clicked.connect(self.browse)
        self.drop.filesDropped.connect(lambda paths: self.set_file(paths[0]))

        self.card = QFrame(self)
        self.card.setProperty("role", "panel")
        self.card.setFixedHeight(64)
        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(14, 8, 10, 8)
        card_layout.setSpacing(12)
        file_icon = QLabel()
        file_icon.setPixmap(icons.pixmap("file-text", 22, icons.ROLE_ACCENT, self.devicePixelRatioF()))
        card_layout.addWidget(file_icon)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.name_label = label("", "cardtitle")
        self.detail_label = label("", "caption")
        texts.addWidget(self.name_label)
        texts.addWidget(self.detail_label)
        card_layout.addLayout(texts, 1)
        change = Button(self.tr("Cambiar..."), "ghost")
        change.clicked.connect(self.browse)
        card_layout.addWidget(change)

        self.stack.addWidget(self.drop)
        self.stack.addWidget(self.card)
        self.stack.setFixedHeight(84)
        self.stack.currentChanged.connect(lambda _: self.stack.setFixedHeight(self.stack.currentWidget().height()))
        layout.addWidget(self.stack)
        if context is not None:
            self._use_context()

    # ---------- estado ----------
    def is_ready(self) -> bool:
        return self.context is not None or bool(self.path)

    def folder(self) -> Path:
        if self.context is not None and self.context.path:
            return Path(self.context.path).parent
        if self.path:
            return Path(self.path).parent
        return Path(settings().last_directory)

    def stem(self) -> str:
        if self.context is not None:
            return Path(self.context.name).stem
        return Path(self.path).stem if self.path else self.tr("documento")

    def value(self):
        """Lo que se pasa al subproceso: ruta o contenido en memoria.

        Del documento abierto se pasa la ruta si no tiene cambios ni cifrado;
        si no, su contenido actual (ya descifrado) en memoria.
        """
        ctx = self.context
        if ctx is not None:
            if ctx.path and not ctx.modified and not ctx.encrypted:
                return ctx.path
            return ctx.snapshot()
        return self.path

    def source_password(self) -> str | None:
        return None if self.context is not None else self.password

    def original_path(self) -> str:
        if self.context is not None:
            return self.context.path
        return self.path

    def preview_document(self) -> pymupdf.Document | None:
        if self.context is not None:
            return self.context.document
        if self._preview_doc is None and self.path:
            try:
                self._preview_doc = open_pdf(self.path, self.password)
            except BlackPdfError:
                self._preview_doc = None
        return self._preview_doc

    # ---------- acciones ----------
    def _use_context(self) -> None:
        ctx = self.context
        self.name = ctx.name
        self.page_count = ctx.page_count
        self.name_label.setText(ctx.name)
        detail = self.tr("{0} páginas").format(ctx.page_count) if ctx.page_count != 1 else self.tr("1 página")
        if ctx.modified:
            detail += "  ·  " + self.tr("con los cambios sin guardar")
        self.detail_label.setText(self.tr("Documento abierto") + "  ·  " + detail)
        self.stack.setCurrentWidget(self.card)

    def browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, self.tr("Elegir PDF"), settings().last_directory, pdf_filter())
        if path:
            settings().last_directory = path
            self.set_file(path)

    def set_file(self, path: str) -> bool:
        password = None
        try:
            if needs_password(path):
                retry = False
                while True:
                    password = ask_password(self, path, retry)
                    if password is None:
                        return False
                    try:
                        doc = open_pdf(path, password)
                        break
                    except WrongPasswordError:
                        retry = True
            else:
                doc = open_pdf(path)
        except BlackPdfError as exc:
            show_error(self, exc)
            return False
        self.context = None
        self.path = path
        self.password = password
        self.page_count = doc.page_count
        self.name = Path(path).name
        if self._preview_doc is not None:
            self._preview_doc.close()
        self._preview_doc = doc
        self.name_label.setText(self.name)
        self.detail_label.setText(
            self.tr("{0} páginas").format(doc.page_count) if doc.page_count != 1 else self.tr("1 página")
        )
        self.stack.setCurrentWidget(self.card)
        self.changed.emit()
        return True


class OutputPicker(QWidget):
    """Destino del resultado. Por defecto, un archivo nuevo junto al original."""

    def __init__(self, mode: str = "save", file_filter: str = "", parent=None):
        super().__init__(parent)
        self.mode = mode
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.picker = PathPicker(mode, file_filter or pdf_filter(), self)
        layout.addWidget(self.picker)
        self._auto = True
        self.picker.edit.textEdited.connect(self._edited)
        self.picker.button.clicked.connect(lambda: setattr(self, "_auto", False))

    def _edited(self, _text: str) -> None:
        self._auto = False

    def suggest(self, path: str) -> None:
        if self._auto or not self.picker.path():
            self.picker.set_path(path)

    def path(self) -> str:
        return self.picker.path()


class ResultPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 24, 0, 8)
        layout.setSpacing(12)
        layout.addStretch(1)
        self.glyph = QLabel()
        self.glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.glyph)
        self.title = label("", "title")
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.title)
        self.detail = label("", "muted", wrap=True)
        self.detail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.detail)
        layout.addStretch(2)

    def show_result(self, title: str, detail: str) -> None:
        self.glyph.setPixmap(icons.pixmap("circle-check", 56, icons.ROLE_SUCCESS, self.devicePixelRatioF()))
        self.title.setText(title)
        self.detail.setText(detail)


class ToolDialog(QDialog):
    """Base de todas las herramientas tipo iLovePDF."""

    openFiles = Signal(list)

    icon_name = "file-text"
    output_mode = "save"
    output_suffix = "resultado"

    def __init__(self, parent=None, context: SourceContext | None = None, preset: dict | None = None):
        super().__init__(parent)
        self.context = context
        self.preset = preset or {}
        self._task: tasks.ProcessTask | None = None
        self._outputs: list[Path] = []
        self.setModal(True)
        self.setMinimumWidth(600)
        self.setWindowTitle(self.title())

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QFrame(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(28, 24, 28, 16)
        header_layout.setSpacing(16)
        header_layout.addWidget(IconTile(self.icon_name), 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        texts.addWidget(label(self.title(), "title"))
        texts.addWidget(label(self.description(), "muted", wrap=True))
        header_layout.addLayout(texts, 1)
        root.addWidget(header)

        self.pages = QStackedWidget(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        self.form = QVBoxLayout(body)
        self.form.setContentsMargins(28, 4, 28, 12)
        self.form.setSpacing(16)
        scroll.setWidget(body)
        self.pages.addWidget(scroll)
        self.result_panel = ResultPanel(self)
        self.pages.addWidget(self.result_panel)
        root.addWidget(self.pages, 1)

        footer = QFrame(self)
        footer.setObjectName("DialogFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(28, 12, 28, 20)
        footer_layout.setSpacing(10)
        self.progress = QProgressBar(footer)
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        footer_layout.addWidget(self.progress)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.status = label("", "caption")
        self.status.setWordWrap(True)
        row.addWidget(self.status, 1)
        self.secondary_button = Button(self.tr("Mostrar en carpeta"), "", "folder-open")
        self.secondary_button.clicked.connect(self._reveal)
        self.secondary_button.setVisible(False)
        self.open_button = Button(self.tr("Abrir en BlackPDF"), "", "external-link")
        self.open_button.clicked.connect(self._open_result)
        self.open_button.setVisible(False)
        self.cancel_button = Button(self.tr("Cancelar"))
        self.cancel_button.clicked.connect(self.reject)
        self.run_button = Button(self.action_text(), "primary")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self.run)
        for button in (self.secondary_button, self.open_button, self.cancel_button, self.run_button):
            row.addWidget(button)
        footer_layout.addLayout(row)
        root.addWidget(footer)

        self.build()
        self.form.addStretch(1)
        self.refresh()
        self._body = body
        self._header = header
        self._footer = footer
        self._fit_to_content()

    def _fit_to_content(self) -> None:
        """Tamaño justo para que no haga falta desplazarse, sin salirse de la pantalla."""
        self._body.adjustSize()
        width = max(self.sizeHint().width(), self._body.sizeHint().width() + 8, 600)
        height = self._header.sizeHint().height() + self._body.sizeHint().height() + self._footer.sizeHint().height() + 8
        screen = self.screen().availableGeometry() if self.screen() is not None else None
        if screen is not None:
            height = min(height, int(screen.height() * 0.9))
            width = min(width, int(screen.width() * 0.9))
        self.resize(width, max(height, 360))

    # ---------- a implementar por cada herramienta ----------
    def title(self) -> str:
        return ""

    def description(self) -> str:
        return ""

    def action_text(self) -> str:
        return self.tr("Procesar")

    def build(self) -> None:
        raise NotImplementedError

    def job(self) -> tuple[str, tuple, dict]:
        """(destino "modulo:funcion", args, kwargs). Lanza ValidationError."""
        raise NotImplementedError

    def describe_result(self, result) -> tuple[str, str, list[Path]]:
        """(título, detalle, archivos generados)."""
        paths = result if isinstance(result, list) else [Path(result)]
        paths = [Path(p) for p in paths]
        if len(paths) == 1:
            return self.tr("Listo"), self.tr("Se ha guardado «{0}».").format(paths[0].name), paths
        return self.tr("Listo"), self.tr("Se han creado {0} archivos en «{1}».").format(
            len(paths), paths[0].parent.name if paths else ""
        ), paths

    def is_ready(self) -> bool:
        return True

    # ---------- ayudas de formulario ----------
    def add_field(self, title: str, widget: QWidget, hint: str = "") -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        if title:
            caption = QLabel(title)
            caption.setStyleSheet("font-weight: 600;")
            layout.addWidget(caption)
        layout.addWidget(widget)
        if hint:
            layout.addWidget(label(hint, "caption", wrap=True))
        self.form.addWidget(block)
        return block

    def add_row(self, *widgets: QWidget, stretch_last: bool = False) -> QWidget:
        holder = QWidget()
        layout = QHBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)
        for widget in widgets:
            layout.addWidget(widget, 1)
        self.form.addWidget(holder)
        return holder

    def field_block(self, title: str, widget: QWidget, hint: str = "") -> QWidget:
        block = QWidget()
        layout = QVBoxLayout(block)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        caption = QLabel(title)
        caption.setStyleSheet("font-weight: 600;")
        layout.addWidget(caption)
        layout.addWidget(widget)
        if hint:
            layout.addWidget(label(hint, "caption", wrap=True))
        return block

    def default_output(self, source: SourcePicker, suffix: str | None = None, extension: str = ".pdf") -> str:
        name = f"{source.stem()}_{suffix or self.output_suffix}{extension}"
        return str(unique_path(source.folder() / name))

    def default_folder(self, source: SourcePicker, suffix: str | None = None) -> str:
        return str(source.folder() / f"{source.stem()}_{suffix or self.output_suffix}")

    def require_output(self, picker: OutputPicker, source_paths: list[str] = ()) -> str:
        path = picker.path()
        if not path:
            raise ValidationError(self.tr("Elige dónde guardar el resultado."))
        if picker.mode == "save":
            if not path.lower().endswith(".pdf"):
                path += ".pdf"
            for original in source_paths:
                if original and Path(original).resolve() == Path(path).resolve():
                    raise ValidationError(
                        self.tr("Elige un nombre distinto al del archivo original para no sobrescribirlo.")
                    )
        return path

    # ---------- ejecución ----------
    def refresh(self) -> None:
        if self._task is None:
            self.run_button.setEnabled(self.is_ready())

    def set_status(self, text: str, error: bool = False) -> None:
        self.status.setText(text)
        self.status.setProperty("role", "error" if error else "caption")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def run(self) -> None:
        if self._task is not None:
            return
        if self.pages.currentWidget() is self.result_panel:
            self.accept()
            return
        try:
            target, args, kwargs = self.job()
        except ValidationError as exc:
            self.set_status(str(exc), error=True)
            return
        except BlackPdfError as exc:
            self.set_status(user_message(exc)[1], error=True)
            return
        self.set_status(self.tr("Procesando..."))
        self.progress.setRange(0, 0)
        self.progress.setVisible(True)
        self.run_button.setEnabled(False)
        self.pages.widget(0).setEnabled(False)
        task = tasks.start(target, *args, **kwargs)
        task.signals.progress.connect(self._on_progress)
        task.signals.succeeded.connect(self._on_success)
        task.signals.failed.connect(self._on_failed)
        task.signals.crashed.connect(self._on_crash)
        task.signals.cancelled.connect(self._on_cancelled)
        self._task = task

    def _finish_task(self) -> None:
        self._task = None
        self.progress.setVisible(False)
        self.pages.widget(0).setEnabled(True)
        self.refresh()

    def _on_progress(self, done: int, total: int) -> None:
        if total > 0:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _on_success(self, result) -> None:
        self._finish_task()
        title, detail, paths = self.describe_result(result)
        self._outputs = [Path(p) for p in paths]
        self.result_panel.show_result(title, detail)
        self.pages.setCurrentWidget(self.result_panel)
        self.set_status("")
        self.cancel_button.setVisible(False)
        self.run_button.setText(self.tr("Hecho"))
        self.run_button.setEnabled(True)
        self.secondary_button.setVisible(bool(self._outputs))
        pdfs = [p for p in self._outputs if p.suffix.lower() == ".pdf"]
        self.open_button.setVisible(0 < len(pdfs) <= 10)
        if self.open_button.isVisible():
            self.open_button.setText(self.tr("Abrir en BlackPDF") if len(pdfs) == 1 else self.tr("Abrir {0} archivos").format(len(pdfs)))

    def _on_failed(self, exc) -> None:
        self._finish_task()
        self.set_status("")
        show_error(self, exc)

    def _on_crash(self, details: str) -> None:
        self._finish_task()
        self.set_status("")
        show_crash(self, details)

    def _on_cancelled(self) -> None:
        self._finish_task()
        self.set_status(self.tr("Operación cancelada."))

    def _reveal(self) -> None:
        if self._outputs:
            reveal_in_folder(self._outputs[0])

    def _open_result(self) -> None:
        pdfs = [str(p) for p in self._outputs if p.suffix.lower() == ".pdf"]
        if pdfs:
            self.openFiles.emit(pdfs)
            self.accept()

    def reject(self) -> None:
        if self._task is not None:
            self._task.cancel()
            return
        super().reject()

    def closeEvent(self, event) -> None:
        if self._task is not None:
            self._task.cancel()
            event.ignore()
            return
        super().closeEvent(event)


class PagePreview(QLabel):
    """Vista previa de una página con las opciones aplicadas (en el hilo de la interfaz)."""

    def __init__(self, render: Callable[[], pymupdf.Pixmap | None], parent=None, size: int = 240):
        super().__init__(parent)
        self._render = render
        self._size = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._update)
        self._image: QImage | None = None

    def schedule(self) -> None:
        self._timer.start()

    def _update(self) -> None:
        try:
            pix = self._render()
        except Exception:
            pix = None
        if pix is None:
            self._image = None
        else:
            self._image = QImage(pix.samples_mv, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
            self._image.setDevicePixelRatio(self.devicePixelRatioF())
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        p = theme.current()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(p.qcolor("viewer"))
        painter.drawRoundedRect(self.rect(), theme.RADIUS, theme.RADIUS)
        if self._image is None:
            painter.setPen(p.qcolor("text_faint"))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, self.tr("Vista previa"))
            return
        w = self._image.width() / self._image.devicePixelRatio()
        h = self._image.height() / self._image.devicePixelRatio()
        x = (self.width() - w) / 2
        y = (self.height() - h) / 2
        shadow = p.qcolor("page_shadow")
        painter.setBrush(shadow)
        painter.drawRect(int(x) - 1, int(y), int(w) + 2, int(h) + 2)
        painter.drawImage(int(x), int(y), self._image)

    def render_scale(self, page_rect: pymupdf.Rect) -> float:
        inner = self._size - 24
        return min(inner / page_rect.width, inner / page_rect.height) * self.devicePixelRatioF()


def preview_page(doc: pymupdf.Document | None, index: int = 0) -> pymupdf.Document | None:
    """Copia de una sola página para aplicar opciones de vista previa."""
    if doc is None or doc.page_count == 0:
        return None
    index = max(0, min(index, doc.page_count - 1))
    single = pymupdf.open()
    single.insert_pdf(doc, from_page=index, to_page=index, annots=True)
    return single

