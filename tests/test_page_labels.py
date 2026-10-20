"""Etiquetas de página."""

from __future__ import annotations

import pymupdf
import pytest

from blackpdf.core import page_labels as pl
from blackpdf.core.errors import InvalidOptionError


def _doc(pages: int = 8) -> pymupdf.Document:
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page()
    return doc


def test_styles():
    assert [pl.format_number("r", n) for n in (1, 4, 9, 14, 2026)] == ["i", "iv", "ix", "xiv", "mmxxvi"]
    assert [pl.format_number("A", n) for n in (1, 26, 27, 53)] == ["A", "Z", "AA", "AAA"]
    assert pl.format_number("", 5) == ""


def test_rules_roundtrip_and_match_mupdf():
    doc = _doc()
    rules = [pl.LabelRule(0, "r"), pl.LabelRule(3, "D", "A-", 101), pl.LabelRule(7, "", "Anexo")]
    pl.set_rules(doc, rules)
    doc = pymupdf.open("pdf", doc.tobytes())
    labels = pl.page_labels(doc)
    assert labels == ["i", "ii", "iii", "A-101", "A-102", "A-103", "A-104", "Anexo"]
    assert labels == [doc[i].get_label() for i in range(doc.page_count)], "igual que MuPDF"
    pl.set_rules(doc, [])
    assert pl.page_labels(doc) is None


def test_pages_before_first_rule_keep_numbers():
    assert pl.labels_from_rules([pl.LabelRule(2, "D", "P", 1)], 4) == ["1", "2", "P1", "P2"]


def test_find_page():
    labels = ["i", "ii", "A-101", "A-102"]
    assert pl.find_page(labels, "a-102", 4) == 3
    assert pl.find_page(labels, "2", 4) == 1, "un número sin etiqueta igual es la página física"
    assert pl.find_page(labels, "Z", 4) is None and pl.find_page(None, "9", 4) is None


def test_invalid_rules():
    with pytest.raises(InvalidOptionError):
        pl.set_rules(_doc(), [pl.LabelRule(0), pl.LabelRule(0, "r")])
    with pytest.raises(InvalidOptionError):
        pl.set_rules(_doc(), [pl.LabelRule(20)])
