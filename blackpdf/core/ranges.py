"""Interpretación de rangos de páginas escritos por el usuario.

Formato (páginas numeradas desde 1):
    "1-3,5,8-10"   rangos y páginas sueltas
    "7-"           de la 7 al final
    "-4"           de la 1 a la 4
    "10-8"         rango descendente (10, 9, 8)
    "fin", "end"   última página (también "1-fin")
Los separadores pueden ser comas, punto y coma o espacios.
"""

from __future__ import annotations

import re

from .errors import InvalidRangeError

_END_WORDS = {"fin", "end", "final", "ultima", "última", "last", "n"}
_SPLIT = re.compile(r"[,;\s]+")
_HYPHEN = re.compile(r"\s*[-\u2013\u2014]\s*")


def _page_number(token: str, page_count: int, spec: str) -> int:
    token = token.strip().lower()
    if token in _END_WORDS:
        return page_count
    if not token.isdigit():
        raise InvalidRangeError(spec=spec, page_count=page_count)
    number = int(token)
    if number < 1 or number > page_count:
        raise InvalidRangeError(spec=spec, page_count=page_count)
    return number


def parse_page_ranges(spec: str, page_count: int) -> list[list[int]]:
    """Devuelve una lista de grupos de índices (base 0), uno por rango."""
    if page_count < 1:
        raise InvalidRangeError(spec=spec, page_count=page_count)
    text = _HYPHEN.sub("-", (spec or "").strip())
    if not text:
        raise InvalidRangeError(spec=spec, page_count=page_count)
    groups: list[list[int]] = []
    for part in _SPLIT.split(text):
        if not part:
            continue
        if "-" in part:
            start_text, _, end_text = part.partition("-")
            start = _page_number(start_text, page_count, spec) if start_text.strip() else 1
            end = _page_number(end_text, page_count, spec) if end_text.strip() else page_count
            step = 1 if end >= start else -1
            groups.append([n - 1 for n in range(start, end + step, step)])
        else:
            groups.append([_page_number(part, page_count, spec) - 1])
    if not groups:
        raise InvalidRangeError(spec=spec, page_count=page_count)
    return groups


def parse_page_selection(spec: str, page_count: int, *, allow_empty_all: bool = False) -> list[int]:
    """Lista ordenada según el texto y sin repetidos. Vacío = todas si se permite."""
    if allow_empty_all and not (spec or "").strip():
        return list(range(page_count))
    seen: set[int] = set()
    result: list[int] = []
    for group in parse_page_ranges(spec, page_count):
        for index in group:
            if index not in seen:
                seen.add(index)
                result.append(index)
    return result


def is_valid_spec(spec: str, page_count: int) -> bool:
    try:
        parse_page_ranges(spec, page_count)
    except InvalidRangeError:
        return False
    return True


def format_page_ranges(indices: list[int] | tuple[int, ...]) -> str:
    """Índices base 0 -> texto compacto: [0, 1, 2, 4] -> "1-3,5"."""
    numbers = sorted({i + 1 for i in indices})
    if not numbers:
        return ""
    parts: list[str] = []
    start = prev = numbers[0]
    for number in numbers[1:] + [None]:
        if number is not None and number == prev + 1:
            prev = number
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if number is not None:
            start = prev = number
    return ",".join(parts)


def chunk_indices(page_count: int, size: int) -> list[list[int]]:
    """Agrupa las páginas de `size` en `size`: 7 páginas, 3 -> [0-2], [3-5], [6]."""
    if size < 1:
        raise InvalidRangeError(spec=str(size), page_count=page_count)
    return [list(range(i, min(i + size, page_count))) for i in range(0, page_count, size)]
