"""Ejecución de operaciones largas en un proceso aparte.

PyMuPDF inicializa MuPDF en modo de un solo hilo, así que no es seguro
usarlo desde varios hilos a la vez. Las herramientas se ejecutan en un
subproceso: la interfaz sigue fluida, el progreso llega por una cola y
cancelar consiste en terminar el proceso.

Este módulo no importa Qt para que el subproceso arranque rápido.
"""

from __future__ import annotations

import importlib
import multiprocessing as mp
import queue as queue_module
import time
import traceback
from dataclasses import dataclass
from typing import Any

from .core.errors import BlackPdfError, OperationCancelled


def _resolve(target: str):
    module_name, _, attribute = target.partition(":")
    if not module_name.startswith("blackpdf.") or not attribute:
        raise ValueError(f"destino no permitido: {target}")
    return getattr(importlib.import_module(module_name), attribute)


def child_main(target: str, args: tuple, kwargs: dict, channel) -> None:
    """Punto de entrada del subproceso."""
    last_sent = 0.0

    def progress(done: int, total: int) -> None:
        nonlocal last_sent
        now = time.monotonic()
        if done >= total or now - last_sent > 0.05:
            last_sent = now
            channel.put(("progress", int(done), int(total)))

    try:
        function = _resolve(target)
        result = function(*args, progress=progress, **kwargs)
        channel.put(("done", result))
    except BlackPdfError as exc:
        channel.put(("error", exc))
    except OperationCancelled:
        channel.put(("cancelled", None))
    except BaseException:
        channel.put(("crash", traceback.format_exc()))


@dataclass
class Message:
    kind: str
    """"progress", "done", "error", "crash" o "cancelled"."""
    payload: Any = None
    total: int = 0


class ProcessJob:
    """Lanza `target` ("modulo:funcion") en un subproceso con `spawn`.

    `spawn` es obligatorio: en Linux, hacer `fork` de un proceso con hilos
    de Qt puede bloquearse, y en Windows es el único método disponible.
    """

    def __init__(self, target: str, args: tuple = (), kwargs: dict | None = None):
        self.target = target
        self.args = args
        self.kwargs = kwargs or {}
        self._context = mp.get_context("spawn")
        self._channel = None
        self._process = None

    def start(self) -> None:
        self._channel = self._context.Queue()
        self._process = self._context.Process(
            target=child_main,
            args=(self.target, self.args, self.kwargs, self._channel),
            daemon=True,
        )
        self._process.start()

    def next_message(self, timeout: float = 0.1) -> Message | None:
        """Siguiente mensaje, o None si no hay. Detecta si el proceso murió."""
        try:
            item = self._channel.get(timeout=timeout)
        except queue_module.Empty:
            if self._process is not None and not self._process.is_alive():
                try:
                    item = self._channel.get(timeout=0.5)
                except queue_module.Empty:
                    code = self._process.exitcode
                    return Message("crash", f"el proceso terminó inesperadamente (código {code})")
            else:
                return None
        kind = item[0]
        if kind == "progress":
            return Message("progress", item[1], item[2])
        return Message(kind, item[1])

    def cancel(self) -> None:
        if self._process is not None and self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
            if self._process.is_alive():
                self._process.kill()

    def close(self) -> None:
        if self._process is not None:
            self._process.join(timeout=1)
            if self._process.is_alive():
                self.cancel()
        if self._channel is not None:
            self._channel.close()
            self._channel.join_thread()


def run_blocking(target: str, *args, **kwargs):
    """Ejecuta en subproceso y espera. Útil para tests y scripts."""
    job = ProcessJob(target, args, kwargs)
    job.start()
    try:
        while True:
            message = job.next_message(0.2)
            if message is None or message.kind == "progress":
                continue
            if message.kind == "done":
                return message.payload
            if message.kind == "error":
                raise message.payload
            if message.kind == "cancelled":
                raise OperationCancelled()
            raise RuntimeError(message.payload)
    finally:
        job.close()
