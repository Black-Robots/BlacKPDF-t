"""Historial de deshacer/rehacer basado en instantáneas completas del PDF."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

DEFAULT_LIMIT = 30


@dataclass(frozen=True)
class Snapshot:
    label: str
    data: bytes


class History:
    """Pila de instantáneas (`doc.tobytes()`) con un máximo de `limit` pasos."""

    def __init__(self, limit: int = DEFAULT_LIMIT):
        if limit < 1:
            raise ValueError("limit debe ser al menos 1")
        self._limit = limit
        self._undo: deque[Snapshot] = deque(maxlen=limit)
        self._redo: deque[Snapshot] = deque(maxlen=limit)

    @property
    def limit(self) -> int:
        return self._limit

    def push(self, label: str, data: bytes) -> None:
        """Guarda el estado previo a una operación. Vacía la pila de rehacer."""
        self._undo.append(Snapshot(label, data))
        self._redo.clear()

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo_label(self) -> str:
        return self._undo[-1].label if self._undo else ""

    def redo_label(self) -> str:
        return self._redo[-1].label if self._redo else ""

    def undo(self, current: bytes) -> bytes:
        """Devuelve el estado anterior y guarda `current` para rehacer."""
        snap = self._undo.pop()
        self._redo.append(Snapshot(snap.label, current))
        return snap.data

    def redo(self, current: bytes) -> bytes:
        snap = self._redo.pop()
        self._undo.append(Snapshot(snap.label, current))
        return snap.data

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()

    def __len__(self) -> int:
        return len(self._undo)
