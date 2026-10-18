"""Lectura de modelos 3D: U3D, PRC y modelos incrustados en PDF."""

from __future__ import annotations

import math
import random
import re
from array import array
from pathlib import Path

import pymupdf
import pytest

from blackpdf.core import model3d
from blackpdf.core.errors import Model3DEmptyError, Model3DError
from blackpdf.core.model3d import bitstream, pdf3d, prc, u3d
from blackpdf.core.model3d.scene import IDENTITY, Scene, build_triangles, invert, multiply, transform_point

from prc_writer import BitWriter, build_prc

DATA = Path(__file__).parent / "data" / "model3d"


def floats(raw: bytes) -> list[float]:
    values = array("f")
    values.frombytes(raw)
    return list(values)


# -- escena ----------------------------------------------------------------------


def test_matrix_helpers_round_trip():
    m = (0.0, 1.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 2.0, 0.0, 5.0, -3.0, 1.0, 1.0)
    point = transform_point(m, (1.0, 2.0, 3.0))
    assert point == pytest.approx((3.0, -2.0, 7.0))
    back = transform_point(invert(m), point)
    assert back == pytest.approx((1.0, 2.0, 3.0))
    assert multiply(IDENTITY, m) == pytest.approx(m)


def test_build_triangles_generates_normals_and_edges():
    positions = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0, 0, 1)]
    triangles = [(0, 1, 2), (0, 2, 3), (0, 4, 1)]
    primitive = build_triangles(positions, triangles)
    assert primitive.vertex_count == 9
    normals = floats(primitive.normals)
    assert normals[0:3] == pytest.approx([0.0, 0.0, 1.0])
    # El pliegue de 90 grados entre el suelo y la pared es una arista viva.
    edges = floats(primitive.edges)
    segments = {tuple(edges[i:i + 6]) for i in range(0, len(edges), 6)}
    assert (0.0, 0.0, 0.0, 1.0, 0.0, 0.0) in segments
    assert len(floats(primitive.wire)) // 6 == 7


# -- decodificador aritmético ---------------------------------------------------------


def test_histogram_frequencies_and_lookup():
    histogram = bitstream._Histogram()
    for symbol in (3, 3, 5, 200):
        histogram.add_symbol(symbol)
    assert histogram.total == 5
    assert histogram.cumulative(3) == 1
    assert histogram.cumulative(4) == 3
    assert histogram.symbol_from(0) == 0
    assert histogram.symbol_from(2) == 3
    assert histogram.symbol_from(3) == 5
    assert histogram.symbol_from(4) == 200


def test_uncompressed_values_read_as_little_endian():
    data = bytes([0x01, 0x02, 0x03, 0x04, 0x05, 0x00, 0x41, 0x42, 0x43, 0x44, 0x45])
    reader = bitstream.BitReader(data)
    assert reader.u32() == 0x04030201
    reader = bitstream.BitReader(bytes([0x03, 0x00]) + b"abc")
    assert reader.string() == "abc"


# -- U3D -----------------------------------------------------------------------------


def test_u3d_box_decodes_geometry_and_material():
    scene = u3d.decode((DATA / "box.u3d").read_bytes())
    assert scene.format == "U3D"
    assert scene.triangle_count == 12
    assert len(scene.parts) == 1
    assert scene.views
    lo, hi = scene.bounds()
    assert all(h > l for l, h in zip(lo, hi))


def _idtf_lists(text: str, key: str, size: int, kind=float) -> list:
    match = re.search(key + r"\s*\{([^}]*)\}", text)
    numbers = [kind(value) for value in match.group(1).split()]
    return [tuple(numbers[i:i + size]) for i in range(0, len(numbers), size)]


def test_u3d_progressive_mesh_matches_idtf_source():
    text = (DATA / "surface.idtf").read_text(encoding="latin-1")
    positions = _idtf_lists(text, "MODEL_POSITION_LIST", 3)
    faces = _idtf_lists(text, "MESH_FACE_POSITION_LIST", 3, int)
    file = u3d.U3DFile((DATA / "surface.u3d").read_bytes())
    mesh = next(iter(file.meshes.values()))
    assert len(mesh.positions) == len(positions)
    assert len(mesh.face_pos) == len(faces)
    mapping = []
    for point in mesh.positions:
        best = min(range(len(positions)), key=lambda i: math.dist(point, positions[i]))
        assert math.dist(point, positions[best]) < 0.01
        mapping.append(best)

    def canonical(tri):
        tri = list(tri)
        k = tri.index(min(tri))
        return tuple(tri[k:] + tri[:k])

    decoded = {canonical(mapping[i] for i in face) for face in mesh.face_pos}
    assert decoded == {canonical(face) for face in faces}
    for normals in mesh.face_norm:
        for corner in normals:
            normal = mesh.normals[corner]
            assert math.isclose(math.sqrt(sum(c * c for c in normal)), 1.0, abs_tol=0.02)


def test_u3d_vertex_colors_are_red_green_blue():
    scene = u3d.decode((DATA / "VertexColor.u3d").read_bytes())
    colored = [p for p in scene.primitives if p.colors]
    assert colored
    values = floats(colored[0].colors)
    pixels = {tuple(round(v, 2) for v in values[i:i + 3]) for i in range(0, len(values), 4)}
    assert (1.0, 0.0, 0.0) in pixels or (0.0, 1.0, 0.0) in pixels


def test_u3d_line_and_point_sets():
    lines = u3d.decode((DATA / "lineset.u3d").read_bytes())
    assert lines.primitives[0].kind == "lines"
    assert lines.primitives[0].vertex_count == 66
    points = u3d.decode((DATA / "pointset.u3d").read_bytes())
    assert points.primitives[0].kind == "points"
    assert points.primitives[0].vertex_count == 18


def test_u3d_rejects_other_data():
    with pytest.raises(u3d.U3DError):
        u3d.decode(b"not a u3d file at all")


def test_u3d_truncated_file_fails_cleanly():
    data = (DATA / "surface.u3d").read_bytes()
    with pytest.raises(u3d.U3DError):
        u3d.decode(data[: len(data) // 2])


# -- PRC ------------------------------------------------------------------------------


def test_prc_doubles_and_integers_round_trip():
    writer = BitWriter()
    random.seed(7)
    values = [0.0, 1.0, -1.0, 0.5, math.pi, -2.5e-7, 1e300, 40.0, 0.1]
    values += [random.uniform(-1e5, 1e5) for _ in range(300)]
    for value in values:
        writer.double(value)
    for value in (0, 1, 255, 256, 2**32 - 1):
        writer.uint(value)
    for value in (0, -1, 127, -128, -129, 2**31 - 1, -(2**31)):
        writer.int(value)
    writer.string("Pieza ñ")
    reader = prc.BitReader(writer.tobytes())
    assert [reader.double() for _ in values] == values
    assert [reader.uint() for _ in range(5)] == [0, 1, 255, 256, 2**32 - 1]
    assert [reader.int() for _ in range(7)] == [0, -1, 127, -128, -129, 2**31 - 1, -(2**31)]
    assert reader.string() == "Pieza ñ"


def test_prc_tessellated_part_with_color_and_location():
    positions = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    data = build_prc(positions, [(0, 1, 2), (0, 2, 3)], color=(0.8, 0.2, 0.1), translate=(10.0, 0.0, 0.0))
    scene = prc.decode(data)
    assert not scene.warnings
    assert scene.triangle_count == 2
    assert len(scene.parts) == 1
    part = scene.parts[0]
    assert part.matrix[12:15] == pytest.approx((10.0, 0.0, 0.0))
    material = scene.materials[scene.primitives[part.primitives[0]].material]
    assert material.diffuse == pytest.approx((0.8, 0.2, 0.1))
    lo, hi = scene.bounds()
    assert lo == pytest.approx((10.0, 0.0, 0.0))
    assert hi == pytest.approx((11.0, 1.0, 0.0))


def test_prc_with_explicit_normals():
    positions = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    normals = [(0, 0, 1), (0, 0, 1), (0, 0, 1)]
    scene = prc.decode(build_prc(positions, [(0, 1, 2)], normals=normals))
    values = floats(scene.primitives[0].normals)
    assert values[:3] == pytest.approx([0.0, 0.0, 1.0])


def test_prc_rejects_other_data():
    with pytest.raises(prc.PRCError):
        prc.decode(b"PDF-1.7 not prc")


# -- PDF -------------------------------------------------------------------------------


def test_find_models_in_pdf_and_load_views():
    doc = pymupdf.open(DATA / "views.pdf")
    found = model3d.find_models(doc)
    assert len(found) == 1
    item = found[0]
    assert item.format == "U3D" and item.kind == "3D"
    assert item.rect == pytest.approx((0, 0, 300, 300))
    embedded = model3d.extract_from_pdf(doc, item.xref)
    scene = model3d.load_embedded(embedded, "views.pdf")
    assert [view.name for view in scene.views][:2] == ["DefaultView", "View1"]
    assert scene.default_view == 0
    assert scene.background == (1.0, 1.0, 1.0)
    assert scene.views[0].camera is not None


def test_pdf_c2w_camera_points_to_target():
    camera = pdf3d._c2w_camera([-1, 0, 0, 0, 0, 1, 0, 1, 0, 0, -27, 0])
    # La cámara está en -Y y mira hacia +Y: su eje Z de OpenGL apunta hacia -Y.
    assert camera[12:15] == pytest.approx((0, -27, 0))
    assert camera[8:11] == pytest.approx((0, -1, 0))
    assert camera[4:7] == pytest.approx((0, 0, 1))
    assert camera[0:3] == pytest.approx((1, 0, 0))


def test_page_models_rect_follows_crop_box():
    doc = pymupdf.open(DATA / "views.pdf")
    page = doc[0]
    xref = model3d.find_models(doc)[0].xref
    doc.xref_set_key(xref, "Rect", "[50 60 200 260]")
    page.set_mediabox(pymupdf.Rect(0, 0, 400, 500))
    page.set_cropbox(pymupdf.Rect(20, 30, 380, 470))
    item = model3d.page_models(doc, 0)[0]
    assert item.rect == pytest.approx((30, 210, 180, 410))


def test_load_file_errors(tmp_path):
    bad = tmp_path / "bad.u3d"
    bad.write_bytes(b"nothing")
    with pytest.raises(Model3DError):
        model3d.load_file(str(bad))
    empty = tmp_path / "empty.prc"
    empty.write_bytes(build_prc([(0, 0, 0)], []))
    with pytest.raises((Model3DEmptyError, Model3DError)):
        model3d.load_file(str(empty))


def test_load_file_prc_and_u3d(tmp_path):
    path = tmp_path / "pieza.prc"
    path.write_bytes(build_prc([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)]))
    scene = model3d.load_file(str(path))
    assert isinstance(scene, Scene) and scene.format == "PRC"
    scene = model3d.load_file(str(DATA / "box.u3d"))
    assert scene.format == "U3D"


def test_load_embedded_runs_in_subprocess():
    from blackpdf.procworker import run_blocking

    doc = pymupdf.open(DATA / "views.pdf")
    embedded = model3d.extract_from_pdf(doc, model3d.find_models(doc)[0].xref)
    scene = run_blocking("blackpdf.core.model3d:load_embedded", embedded, "views.pdf")
    assert scene.triangle_count == 12
