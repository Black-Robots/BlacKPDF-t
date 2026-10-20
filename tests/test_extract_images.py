"""Extraer imágenes: calidad original, sin repetidas, con transparencia y filtro de tamaño."""

from __future__ import annotations

import pymupdf

from blackpdf.core.images import extract_images


def _png(width: int, height: int, alpha: bool) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), alpha)
    pix.set_rect(pix.irect, (200, 30, 40, 128) if alpha else (30, 120, 200))
    return pix.tobytes("png")


def _jpeg(width: int, height: int) -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, width, height), False)
    pix.set_rect(pix.irect, (10, 200, 90))
    return pix.tobytes("jpeg")


def test_extracts_each_image_once_with_original_format(tmp_path):
    doc = pymupdf.open()
    photo = _jpeg(300, 200)
    for index in range(2):
        page = doc.new_page()
        page.insert_image(pymupdf.Rect(50, 50, 350, 250), stream=photo)  # la misma foto en dos páginas
    doc[1].insert_image(pymupdf.Rect(50, 300, 250, 500), stream=_png(200, 200, alpha=True))
    doc[1].insert_image(pymupdf.Rect(400, 50, 416, 66), stream=_png(16, 16, alpha=False))  # icono
    source = tmp_path / "fotos.pdf"
    doc.save(source)

    outputs = extract_images(str(source), str(tmp_path / "todas"))
    assert sorted(p.suffix for p in outputs) == [".jpg", ".png", ".png"], "la foto repetida sale una vez"
    jpg = next(p for p in outputs if p.suffix == ".jpg")
    assert jpg.read_bytes() == photo, "la foto sale tal cual, sin recomprimir"
    transparent = [pymupdf.Pixmap(str(p)) for p in outputs if p.suffix == ".png"]
    assert any(pix.alpha for pix in transparent), "la transparencia se conserva"

    big = extract_images(str(source), str(tmp_path / "grandes"), min_size=64)
    assert len(big) == 2, "se omite el icono de 16 px"
    only_first = extract_images(str(source), str(tmp_path / "primera"), pages="1")
    assert len(only_first) == 1
