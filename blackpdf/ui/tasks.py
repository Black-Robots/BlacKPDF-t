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

    def _emit(self, signal, *args) -> None:
        """Emite ignorando el caso de que la interfaz ya se haya cerrado."""
        try:
            signal.emit(*args)
        except RuntimeError:
            pass

    def run(self) -> None:
        signals = self.signals
        try:
            self._job.start()
            while True:
                if self._cancel.is_set():
                    self._job.cancel()
                    self._emit(signals.cancelled)
                    return
                message = self._job.next_message(0.1)
                if message is None:
                    continue
                if message.kind == "progress":
                    self._emit(signals.progress, int(message.payload), int(message.total))
                elif message.kind == "done":
                    self._emit(signals.succeeded, message.payload)
                    return
                elif message.kind == "error":
                    self._emit(signals.failed, message.payload)
                    return
                elif message.kind == "cancelled":
                    self._emit(signals.cancelled)
                    return
                else:
                    self._emit(signals.crashed, str(message.payload))
                    return
        except Exception as exc:  # no se pudo lanzar el subproceso
            self._emit(signals.crashed, repr(exc))
        finally:
            try:
                self._job.close()
            except Exception:
                pass
            self._emit(signals.finished)


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


def shutdown(timeout_ms: int = 3000) -> None:
    """Cancela las tareas y espera a que sus hilos terminen (al cerrar la aplicación)."""
    cancel_all()
    QThreadPool.globalInstance().waitForDone(timeout_ms)
