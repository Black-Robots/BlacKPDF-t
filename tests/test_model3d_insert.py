"""Meter piezas 3D en un PDF: se encuentran, se leen con su vista y la portada se ve."""

from __future__ import annotations

import struct
from pathlib import Path

import pymupdf
import pytest

from blackpdf.core import model3d
from blackpdf.core.model3d import insert

DATA = Path(__file__).parent / "data" / "model3d"
CUBE = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def _stl(path: Path) -> Path:
    tris = [(q[0], q[1], q[2]) for q in QUADS] + [(q[0], q[2], q[3]) for q in QUADS]
    data = bytearray(b"\0" * 80 + struct.pack("<I", len(tris)))
    for tri in tris:
        data += struct.pack("<12fH", 0, 0, 0, *CUBE[tri[0]], *CUBE[tri[1]], *CUBE[tri[2]], 0)
    path.write_bytes(bytes(data))
    return path


def _inside_is_drawn(page: pymupdf.Page, rect: pymupdf.Rect) -> bool:
    """La portada pinta algo distinto del fondo blanco en el centro del recuadro."""
    pix = page.get_pixmap(dpi=72)
    x, y = int(rect.x0 + rect.width / 2), int(rect.y0 + rect.height / 2)
    return pix.pixel(x, y)[:3] != (255, 255, 255)


def test_prepare_mesh_makes_prc_and_poster(tmp_path):
    prepared = insert.prepare(str(_stl(tmp_path / "cubo.stl")), aspect=2.0)
    assert prepared.format == "PRC" and prepared.data[:3] == b"PRC"
    assert prepared.triangles == 12 and prepared.parts == 1
    assert prepared.poster[:2] == b"\xff\xd8" and prepared.poster_size == (1400, 700)
    assert len(prepared.c2w) == 12 and prepared.orbit > 0


def test_inserted_model_is_found_and_read_with_its_view(tmp_path):
    doc = pymupdf.open()
    doc.new_page()
    rect = pymupdf.Rect(72, 100, 472, 400)
    prepared = insert.prepare(str(_stl(tmp_path / "cubo.stl")), aspect=rect.width / rect.height)
    insert.insert_model(doc, 0, rect, prepared)
    reopened = pymupdf.open("pdf", doc.tobytes(garbage=3, deflate=True))
    found = model3d.find_models(reopened)
    assert len(found) == 1 and found[0].format == "PRC" and found[0].page == 0
    scene = model3d.load_embedded(model3d.extract_from_pdf(reopened, found[0].xref), "cubo.stl")
    assert scene.triangle_count == 12
    assert scene.views and scene.views[scene.default_view].name == "Vista inicial"
    assert scene.views[scene.default_view].camera is not None
    assert scene.background == (1.0, 1.0, 1.0)
    assert _inside_is_drawn(reopened[0], rect), "la portada se ve en la página"


def test_rotated_page_and_existing_annotations(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.add_text_annot((30, 30), "nota previa")
    page.set_rotation(90)
    rect = pymupdf.Rect(100, 80, 600, 400)  # coordenadas visibles (página apaisada al girarla)
    prepared = insert.prepare(str(_stl(tmp_path / "cubo.stl")), aspect=rect.width / rect.height)
    insert.insert_model(doc, 0, rect, prepared)
    page = doc[0]
    kinds = [annot.type[1] for annot in page.annots()]
    assert "Text" in kinds, "las anotaciones que había se conservan"
    assert len(model3d.find_models(doc)) == 1
    assert _inside_is_drawn(page, rect)


def test_new_page_and_native_formats(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=842, height=595)
    index, rect = insert.new_page_rect(doc, 0)
    assert index == 1 and doc.page_count == 2 and doc[1].rect.contains(rect)
    for source in (DATA / "box.u3d", DATA / "compressed.prc"):
        prepared = insert.prepare(str(source), aspect=rect.width / rect.height)
        assert prepared.format == source.suffix[1:].upper() and prepared.data == source.read_bytes()
        insert.insert_model(doc, index, rect, prepared)
    assert [m.format for m in model3d.find_models(doc)] == ["U3D", "PRC"]


def test_unknown_format(tmp_path):
    other = tmp_path / "pieza.dwg"
    other.write_bytes(b"AC1032")
    with pytest.raises(model3d.Model3DError if hasattr(model3d, "Model3DError") else Exception):
        insert.prepare(str(other))
