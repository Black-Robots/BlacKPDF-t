"""Números, tamaños y fechas con el formato del idioma de la interfaz."""

from __future__ import annotations

import datetime as dt

from PySide6.QtCore import QDate, QDateTime, QLocale

from ..core.files import human_size


def number(value: int) -> str:
    """Entero con separador de miles: 12.345 / 12,345 / 12 345."""
    return QLocale().toString(int(value))


def decimal(value: float, digits: int = 1) -> str:
    """Número con decimales sin ceros sobrantes: 1,5 / 1.5 / 2."""
    text = QLocale().toString(float(value), "f", digits)
    point = QLocale().decimalPoint()
    if point in text:
        text = text.rstrip("0").rstrip(point)
    return text


def percent(value: float) -> str:
    """Porcentaje con la tipografía del idioma: «100 %» (es, fr) o «100%» (en, pt)."""
    from .i18n import current

    text = QLocale().toString(round(value))
    return f"{text} %" if current() in ("es", "fr") else f"{text}%"


def size(num_bytes: int) -> str:
    return human_size(num_bytes, QLocale().decimalPoint())


def date(value: dt.date | dt.datetime) -> str:
    return QLocale().toString(QDate(value.year, value.month, value.day), QLocale.FormatType.ShortFormat)


def date_time(value: dt.datetime) -> str:
    if value.tzinfo is not None:
        value = value.astimezone()
    stamp = QDateTime(QDate(value.year, value.month, value.day), _time(value))
    return QLocale().toString(stamp, QLocale.FormatType.ShortFormat)


def _time(value: dt.datetime):
    from PySide6.QtCore import QTime

    return QTime(value.hour, value.minute, value.second)
