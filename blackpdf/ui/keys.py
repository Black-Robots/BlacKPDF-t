"""Atajos de teclado escritos como en los teclados de cada idioma."""

from __future__ import annotations

from PySide6.QtCore import QT_TRANSLATE_NOOP, QCoreApplication
from PySide6.QtGui import QKeySequence

# Nombres cortos de teclas (los de Qt son largos: «Control+Mayúsculas+S»).
_KEY_NAMES = (
    ("Shift", QT_TRANSLATE_NOOP("Keys", "Mayús")),
    ("Del", QT_TRANSLATE_NOOP("Keys", "Supr")),
    ("PgDown", QT_TRANSLATE_NOOP("Keys", "AvPág")),
    ("PgUp", QT_TRANSLATE_NOOP("Keys", "RePág")),
    ("Return", QT_TRANSLATE_NOOP("Keys", "Intro")),
    ("Backspace", QT_TRANSLATE_NOOP("Keys", "Retroceso")),
)


def shortcut_text(sequence: QKeySequence | str) -> str:
    """Atajo en su forma corta habitual: Ctrl+Mayús+S, Supr, AvPág (o Shift, Del, PgDn...)."""
    text = QKeySequence(sequence).toString(QKeySequence.SequenceFormat.PortableText)
    for english, name in _KEY_NAMES:
        text = text.replace(english, QCoreApplication.translate("Keys", name))
    return text
