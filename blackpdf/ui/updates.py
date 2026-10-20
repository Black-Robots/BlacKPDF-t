"""Versiones nuevas: comprobación en segundo plano y ventana para actualizar."""

from __future__ import annotations

import os
import time
from pathlib import Path

from PySide6.QtCore import QObject, QStandardPaths, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QProgressBar, QTextBrowser, QVBoxLayout

from .. import __version__
from ..core import errors as core_errors
from ..core import updates as core
from . import tasks
from .errors import show_error
from .settings import settings
from .widgets import Button, label

CHECK_INTERVAL = 20 * 3600
"""Como mucho una comprobación automática al día."""


def check_due() -> bool:
    return settings().check_updates and time.time() - settings().last_update_check > CHECK_INTERVAL


class UpdateChecker(QObject):
    """Pregunta a GitHub, en un subproceso, si hay una versión más nueva que la instalada."""

    available = Signal(object)
    upToDate = Signal()
    failed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._task = None

    def check(self) -> None:
        if self._task is not None:
            return
        task = tasks.start("blackpdf.core.updates:fetch_latest")
        task.signals.succeeded.connect(self._checked)
        task.signals.failed.connect(self.failed)
        task.signals.crashed.connect(lambda details: self.failed.emit(core_errors.UpdateCheckError(details)))
        task.signals.finished.connect(self._finished)
        self._task = task

    def _checked(self, release) -> None:
        settings().last_update_check = time.time()
        if release is not None and core.is_newer(release.version, __version__):
            self.available.emit(release)
        else:
            self.upToDate.emit()

    def _finished(self) -> None:
        self._task = None


def _downloads_folder() -> Path:
    folder = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DownloadLocation)
    return Path(folder) if folder else Path.home()


class UpdateDialog(QDialog):
    """Novedades de la versión nueva y botón para actualizar según cómo se instaló BlackPDF."""

    installRequested = Signal(str, str)
    """(tipo de instalación, ruta del archivo descargado): la ventana principal termina la actualización."""

    def __init__(self, release: core.Release, parent=None, kind: str | None = None):
        super().__init__(parent)
        self.release = release
        self.kind = kind or core.install_kind()
        if self.kind != core.KIND_MANUAL and core.asset_for(release, self.kind) is None:
            self.kind = core.KIND_MANUAL
        self._task = None
        self.setWindowTitle(self.tr("Actualizar BlackPDF"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(10)
        layout.addWidget(label(self.tr("BlackPDF {0} ya está disponible").format(release.version), "title"))
        layout.addWidget(label(self.tr("Tienes la versión {0}. Estas son las novedades:").format(__version__), "body"))
        notes = QTextBrowser(self)
        notes.setOpenExternalLinks(True)
        notes.setMarkdown(release.notes.strip() or self.tr("Esta versión no tiene notas."))
        notes.setMinimumHeight(220)
        layout.addWidget(notes, 1)
        self.progress = QProgressBar(self)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(6)
        self.progress.hide()
        layout.addWidget(self.progress)
        self.status = label("", "caption", wrap=True)
        self.status.hide()
        layout.addWidget(self.status)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.skip_button = Button(self.tr("Omitir esta versión"), "ghost")
        self.skip_button.clicked.connect(self._skip)
        row.addWidget(self.skip_button)
        row.addStretch(1)
        self.later_button = Button(self.tr("Más tarde"))
        self.later_button.clicked.connect(self._later)
        row.addWidget(self.later_button)
        texts = {
            core.KIND_WINDOWS: self.tr("Actualizar ahora"),
            core.KIND_APPIMAGE: self.tr("Actualizar ahora"),
            core.KIND_DEB: self.tr("Descargar e instalar"),
            core.KIND_MANUAL: self.tr("Ir a la descarga"),
        }
        self.update_button = Button(texts[self.kind], "primary")
        self.update_button.setDefault(True)
        self.update_button.clicked.connect(self._update)
        row.addWidget(self.update_button)
        layout.addLayout(row)
        self.update_button.setFocus()

    # ---------- acciones ----------
    def _skip(self) -> None:
        settings().skipped_version = self.release.version
        self.reject()

    def _later(self) -> None:
        if self._task is not None:
            self._task.cancel()
            return
        self.reject()

    def _folder(self) -> Path:
        if self.kind == core.KIND_APPIMAGE:
            # Junto al AppImage actual: así sustituirlo es instantáneo y no cambia de sitio.
            folder = Path(os.environ.get("APPIMAGE", "")).parent
            if folder.is_dir() and os.access(folder, os.W_OK):
                return folder
        if self.kind == core.KIND_WINDOWS:
            temp = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.TempLocation)
            return Path(temp) / "BlackPDF-update"
        return _downloads_folder()

    def _update(self) -> None:
        if self.kind == core.KIND_MANUAL:
            QDesktopServices.openUrl(QUrl(self.release.page))
            self.accept()
            return
        self.update_button.setEnabled(False)
        self.skip_button.setEnabled(False)
        self.later_button.setText(self.tr("Cancelar"))
        self.progress.setRange(0, 0)
        self.progress.show()
        self.status.setText(self.tr("Descargando y comprobando la versión {0}...").format(self.release.version))
        self.status.show()
        task = tasks.start("blackpdf.core.updates:download", self.release, self.kind, str(self._folder()))
        task.signals.progress.connect(self._progress)
        task.signals.succeeded.connect(self._downloaded)
        task.signals.failed.connect(self._failed)
        task.signals.crashed.connect(lambda details: self._failed(core_errors.UpdateDownloadError(details)))
        task.signals.cancelled.connect(self._reset)
        task.signals.finished.connect(self._task_finished)
        self._task = task

    def _progress(self, done: int, total: int) -> None:
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(done)

    def _task_finished(self) -> None:
        self._task = None

    def _reset(self) -> None:
        self.update_button.setEnabled(True)
        self.skip_button.setEnabled(True)
        self.later_button.setText(self.tr("Más tarde"))
        self.progress.hide()
        self.status.hide()

    def _failed(self, exc) -> None:
        self._reset()
        show_error(self, exc)

    def _downloaded(self, path: str) -> None:
        if self.kind == core.KIND_APPIMAGE:
            current = os.environ.get("APPIMAGE", "")
            if Path(path).parent == Path(current).parent:
                try:
                    core.replace_appimage(path, current)
                except core_errors.BlackPdfError as exc:
                    self._failed(exc)
                    return
                self.accept()
                self.installRequested.emit(self.kind, current)
                return
            # Sin permiso para escribir junto al AppImage: se deja en Descargas.
            self._reset()
            QMessageBox.information(self, self.tr("Actualizar BlackPDF"), self.tr(
                "La versión nueva se ha descargado en «{0}». Ábrela desde ahí; ya puedes borrar la anterior."
            ).format(path))
            self.accept()
            return
        if self.kind == core.KIND_DEB:
            self._reset()
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
            QMessageBox.information(self, self.tr("Actualizar BlackPDF"), self.tr(
                "Se ha abierto el instalador del sistema con la versión nueva. Cuando termine, vuelve a abrir BlackPDF."
            ))
            self.accept()
            return
        self.accept()
        self.installRequested.emit(self.kind, path)

    def reject(self) -> None:
        if self._task is not None:
            self._task.cancel()
        super().reject()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self._task is not None:
            self._task.cancel()
            return
        super().keyPressEvent(event)
