"""Etiquetas de página: «i, ii, iii», «A-101, A-102»... en lugar de 1, 2, 3.

Cada regla empieza en una página y define estilo, prefijo y primer número. Es como
numeran los libros (prólogo en romanos) y los juegos de planos (hojas A-101...).
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from .errors import InvalidOptionError

STYLES = ("D", "r", "R", "a", "A", "")
"""Decimal, romanos en minúscula y mayúscula, letras en minúscula y mayúscula, solo prefijo."""


@dataclass
class LabelRule:
    start: int
    """Índice de la primera página (desde 0)."""
    style: str = "D"
    prefix: str = ""
    first: int = 1


def _roman(number: int) -> str:
    result = []
    for value, letters in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                           (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while number >= value:
            result.append(letters)
            number -= value
    return "".join(result)


def _letters(number: int) -> str:
    # Como en el PDF: a..z, luego aa..zz, aaa...
    return chr(ord("a") + (number - 1) % 26) * ((number - 1) // 26 + 1)


def format_number(style: str, number: int) -> str:
    if style == "D":
        return str(number)
    if style == "r":
        return _roman(number)
    if style == "R":
        return _roman(number).upper()
    if style == "a":
        return _letters(number)
    if style == "A":
        return _letters(number).upper()
    return ""


def read_rules(doc: pymupdf.Document) -> list[LabelRule]:
    rules = []
    for item in doc.get_page_labels() or []:
        rules.append(LabelRule(int(item.get("startpage", 0)), item.get("style", "") or "",
                               item.get("prefix", "") or "", int(item.get("firstpagenum", 1) or 1)))
    return sorted(rules, key=lambda rule: rule.start)


def labels_from_rules(rules: list[LabelRule], page_count: int) -> list[str]:
    """Etiqueta de cada página; las anteriores a la primera regla llevan su número."""
    result = [str(i + 1) for i in range(page_count)]
    ordered = sorted(rules, key=lambda rule: rule.start)
    for position, rule in enumerate(ordered):
        end = ordered[position + 1].start if position + 1 < len(ordered) else page_count
        for index in range(max(0, rule.start), min(end, page_count)):
            result[index] = rule.prefix + format_number(rule.style, rule.first + index - rule.start)
    return result


def page_labels(doc: pymupdf.Document) -> list[str] | None:
    """Etiquetas del documento, o None si no tiene (entonces se usan los números de siempre)."""
    rules = read_rules(doc)
    return labels_from_rules(rules, doc.page_count) if rules else None


def validate(rules: list[LabelRule], page_count: int) -> list[LabelRule]:
    starts = set()
    for rule in rules:
        if not 0 <= rule.start < max(page_count, 1) or rule.start in starts:
            raise InvalidOptionError(f"regla en la página {rule.start + 1}", option="start")
        if rule.style not in STYLES or rule.first < 1:
            raise InvalidOptionError(f"estilo {rule.style!r}", option="style")
        starts.add(rule.start)
    return sorted(rules, key=lambda rule: rule.start)


def set_rules(doc: pymupdf.Document, rules: list[LabelRule]) -> None:
    """Sustituye las etiquetas del documento (sin reglas, se quitan)."""
    rules = validate(rules, doc.page_count)
    doc.set_page_labels([{"startpage": r.start, "style": r.style, "prefix": r.prefix, "firstpagenum": r.first}
                         for r in rules])


def find_page(labels: list[str] | None, text: str, page_count: int) -> int | None:
    """Página que corresponde a lo escrito: primero la etiqueta exacta y, si no, el número de página."""
    text = text.strip()
    if not text:
        return None
    if labels:
        lowered = text.casefold()
        for index, value in enumerate(labels):
            if value.casefold() == lowered:
                return index
    if text.isdigit() and 1 <= int(text) <= page_count:
        return int(text) - 1
    return None
