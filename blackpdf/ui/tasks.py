"""Tareas largas: un hilo del QThreadPool supervisa un subproceso de trabajo.

El hilo nunca toca PyMuPDF (no es seguro entre hilos); solo lanza el
subproceso, reenvía su progreso como señales Qt y lo termina si se cancela.
"""

from __future__ import annotations

import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..procworker import ProcessJob


class TaskSignals(QObject):
    progress = Signal(int, int)
    succeeded = Signal(object)
    failed = Signal(object)
    """Excepción prevista (BlackPdfError) del subproceso."""
    crashed = Signal(str)
    """Traza de un fallo inesperado; se registra pero no se muestra."""
    cancelled = Signal()
    finished = Signal()


class ProcessTask(QRunnable):
    def __init__(self, target: str, args: tuple = (), kwargs: dict | None = None):
        super().__init__()
        self.setAutoDelete(False)
        self.signals = TaskSignals()
        self._job = ProcessJob(target, args, kwargs)
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def run(self) -> None:
        try:
            self._job.start()
            while True:
                if self._cancel.is_set():
                    self._job.cancel()
                    self.signals.cancelled.emit()
                    return
                message = self._job.next_message(0.1)
                if message is None:
                    continue
                if message.kind == "progress":
                    self.signals.progress.emit(int(message.payload), int(message.total))
                elif message.kind == "done":
                    self.signals.succeeded.emit(message.payload)
                    return
                elif message.kind == "error":
                    self.signals.failed.emit(message.payload)
                    return
                elif message.kind == "cancelled":
                    self.signals.cancelled.emit()
                    return
                else:
                    self.signals.crashed.emit(str(message.payload))
                    return
        except Exception as exc:  # no se pudo lanzar el subproceso
            self.signals.crashed.emit(repr(exc))
        finally:
            try:
                self._job.close()
            finally:
                self.signals.finished.emit()


_running: set[ProcessTask] = set()


def start(target: str, *args, **kwargs) -> ProcessTask:
    """Lanza `target` ("blackpdf.core.modulo:funcion") en segundo plano."""
    task = ProcessTask(target, args, kwargs)
    _running.add(task)
    task.signals.finished.connect(lambda: _running.discard(task))
    QThreadPool.globalInstance().start(task)
    return task


def cancel_all() -> None:
    for task in list(_running):
        task.cancel()
