"""Notificación de progreso para operaciones largas."""

from __future__ import annotations

from collections.abc import Callable

ProgressCallback = Callable[[int, int], None]
"""Recibe (pasos hechos, pasos totales). Puede lanzar OperationCancelled."""


def report(progress: ProgressCallback | None, done: int, total: int) -> None:
    if progress is not None:
        progress(done, max(total, 1))
