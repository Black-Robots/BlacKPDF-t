"""PDF e imágenes de prueba generados en cada test."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest


def page_labels(doc_or_path) -> list[str]:
    """Texto "P<n>" de cada página, para comprobar el orden."""
    doc = pymupdf.open(doc_or_path) if isinstance(doc_or_path, (str, Path)) else doc_or_path
    labels = []
    for page in doc:
        words = [w[4] for w in page.get_text("words") if w[4].startswith("P")]
        labels.append(words[0] if words else "")
    return labels


def build_pdf(pages: int = 3, size=(595, 842), prefix: str = "P") -> pymupdf.Document:
    doc = pymupdf.open()
    for number in range(1, pages + 1):
        page = doc.new_page(width=size[0], height=size[1])
        page.insert_text((72, 100), f"{prefix}{number}", fontsize=24)
        page.insert_text((72, 140), "texto de ejemplo para buscar", fontsize=12)
    return doc


@pytest.fixture
def make_pdf(tmp_path):
    counter = {"n": 0}

    def factory(pages: int = 3, size=(595, 842), prefix: str = "P", name: str | None = None) -> Path:
        counter["n"] += 1
        path = tmp_path / (name or f"doc{counter['n']}.pdf")
        doc = build_pdf(pages, size, prefix)
        doc.save(path)
        doc.close()
        return path

    return factory


def build_image(width: int = 120, height: int = 80, gray: int = 90) -> pymupdf.Pixmap:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.clear_with(gray)
    return pix


@pytest.fixture
def make_image(tmp_path):
    counter = {"n": 0}

    def factory(width: int = 120, height: int = 80, fmt: str = "png", gray: int = 90) -> Path:
        counter["n"] += 1
        path = tmp_path / f"img{counter['n']}.{fmt}"
        pix = build_image(width, height, gray)
        if fmt in ("jpg", "jpeg"):
            pix.save(str(path), output="jpg")
        else:
            pix.save(str(path), output=fmt)
        return path

    return factory


def dark_bbox(page: pymupdf.Page, threshold: int = 200):
    """Caja de píxeles oscuros de un render a 72 ppp (coordenadas visibles)."""
    pix = page.get_pixmap(colorspace=pymupdf.csGRAY, alpha=False)
    xs, ys = [], []
    for y in range(pix.height):
        row = pix.samples[y * pix.stride : y * pix.stride + pix.width]
        for x, value in enumerate(row):
            if value < threshold:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)
