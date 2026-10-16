"""Fuentes para estampar texto y utilidades de texto de página."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import pymupdf

FONTS_DIR = Path(__file__).resolve().parent.parent / "resources" / "fonts"

FONT_CHOICES: dict[str, str] = {
    "helv": "Helvetica",
    "hebo": "Helvetica Negrita",
    "tiro": "Times",
    "tibo": "Times Negrita",
    "cour": "Courier",
    "inter": "Inter",
    "inter-bold": "Inter Negrita",
}

_EMBEDDED = {
    "inter": "Inter-Regular.ttf",
    "inter-bold": "Inter-Bold.ttf",
}


@lru_cache(maxsize=16)
def _load_font(name: str, file: str) -> pymupdf.Font:
    if file:
        return pymupdf.Font(fontfile=file)
    return pymupdf.Font(fontname=name)


def _latin_only(text: str) -> bool:
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def font_for(key: str, font_file: str = "", text: str = "") -> tuple[pymupdf.Font, str, str | None]:
    """Devuelve (Font, nombre para insert_text, archivo o None).

    Las fuentes base-14 no se incrustan (archivo más pequeño) pero solo
    cubren el alfabeto latino; si `text` tiene otros caracteres se usa la
    fuente Inter incluida con la aplicación.
    """
    if font_file:
        return _load_font("", font_file), "custom", font_file
    if key in _EMBEDDED or (text and not _latin_only(text)):
        file_name = _EMBEDDED.get(key, "Inter-Regular.ttf")
        path = str(FONTS_DIR / file_name)
        return _load_font("", path), key if key in _EMBEDDED else "inter", path
    name = key if key in FONT_CHOICES else "helv"
    return _load_font(name, ""), name, None


@dataclass(frozen=True)
class Word:
    rect: pymupdf.Rect
    text: str
    block: int
    line: int
    number: int


def page_words(page: pymupdf.Page) -> list[Word]:
    """Palabras de la página en orden de lectura, en coordenadas visibles."""
    matrix = page.rotation_matrix
    words = page.get_text("words", sort=False)
    return [Word(pymupdf.Rect(w[:4]) * matrix, w[4], w[5], w[6], w[7]) for w in words]


def _nearest_word(words: list[Word], point: pymupdf.Point) -> int:
    best, best_distance = 0, float("inf")
    for index, word in enumerate(words):
        if point in word.rect:
            return index
        dx = max(word.rect.x0 - point.x, 0, point.x - word.rect.x1)
        dy = max(word.rect.y0 - point.y, 0, point.y - word.rect.y1)
        distance = dx * dx + dy * dy * 4
        if distance < best_distance:
            best, best_distance = index, distance
    return best


def select_words(words: list[Word], start: pymupdf.Point, stop: pymupdf.Point) -> list[Word]:
    """Palabras entre dos puntos siguiendo el orden de lectura."""
    if not words:
        return []
    a = _nearest_word(words, pymupdf.Point(start))
    b = _nearest_word(words, pymupdf.Point(stop))
    if a > b:
        a, b = b, a
    return words[a : b + 1]


def words_text(words: list[Word]) -> str:
    """Texto de las palabras respetando saltos de línea y de bloque."""
    parts: list[str] = []
    previous: Word | None = None
    for word in words:
        if previous is not None:
            if word.block != previous.block:
                parts.append("\n\n")
            elif word.line != previous.line:
                parts.append("\n")
            else:
                parts.append(" ")
        parts.append(word.text)
        previous = word
    return "".join(parts)


def line_rects(words: list[Word]) -> list[pymupdf.Rect]:
    """Une las palabras de cada línea en un único rectángulo de resaltado."""
    rects: list[pymupdf.Rect] = []
    key = None
    for word in words:
        current = (word.block, word.line)
        if current == key and rects:
            rects[-1] |= word.rect
        else:
            rects.append(pymupdf.Rect(word.rect))
            key = current
    return rects
