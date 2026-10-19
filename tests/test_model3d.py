"""Lectura de modelos 3D: U3D, PRC y modelos incrustados en PDF."""

from __future__ import annotations

import math
import random
import re
import struct
from array import array
from pathlib import Path

import pymupdf
import pytest

from blackpdf.core import model3d
from blackpdf.core.errors import Model3DEmptyError, Model3DError
from blackpdf.core.model3d import bitstream, pdf3d, prc, prc_native, u3d
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


# -- PRC con la biblioteca nativa (nanoPRC) ---------------------------------------------

native = pytest.mark.skipif(not prc_native.available(), reason="biblioteca nativa blackprc sin compilar")


def _block(parts) -> bytes:
    """Bloque binario como el que devuelve blackprc (formato versión 1)."""
    out = b"BPRC" + struct.pack("<II", 1, len(parts))
    for name, matrix, batches in parts:
        raw = name.encode("utf-8")
        out += struct.pack("<I", len(raw)) + raw + struct.pack("<16d", *matrix) + struct.pack("<II", 1, len(batches))
        for kind, color, positions in batches:
            flat = [c for p in positions for c in p]
            out += struct.pack("<II4fI", kind, 0, *color, len(positions)) + struct.pack(f"<{len(flat)}f", *flat)
            if kind == 0:
                out += struct.pack(f"<{len(flat)}f", *([0.0, 0.0, 1.0] * len(positions)))
                out += struct.pack("<I", 2) + struct.pack("<6f", *flat[:6])
                out += struct.pack("<I", 0)
    return out


def test_native_block_becomes_scene_with_shared_materials():
    red = (0.75, 0.25, 0.25, 1.0)
    triangle = [(0, 0, 0), (1, 0, 0), (0, 1, 0)]
    moved = (1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 5.0, 0, 0, 1.0)
    blob = _block([
        ("Tornillo", IDENTITY, [(0, red, triangle), (1, red, triangle[:2])]),
        ("Tornillo", moved, [(0, red, triangle)]),
        ("", IDENTITY, [(0, (0.0, 0.0, 1.0, 0.5), triangle)]),
    ])
    scene = prc_native.parse(blob)
    assert [part.name for part in scene.parts] == ["Tornillo", "Tornillo (2)", "Pieza 3"]
    assert scene.triangle_count == 3
    assert scene.parts[1].matrix[12] == 5.0
    kinds = [scene.primitives[i].kind for i in scene.parts[0].primitives]
    assert kinds == ["triangles", "lines"]
    # Mismo color, mismo material; las líneas no se iluminan.
    first, line = (scene.primitives[i] for i in scene.parts[0].primitives)
    assert first.material == scene.primitives[scene.parts[1].primitives[0]].material
    assert scene.materials[first.material].diffuse == pytest.approx(red[:3])
    assert not scene.materials[line.material].lighting
    translucent = scene.materials[scene.primitives[scene.parts[2].primitives[0]].material]
    assert translucent.opacity == pytest.approx(0.5)
    assert len(first.wire) == 24 and first.edges == b""


def test_native_block_rejects_corrupt_data():
    with pytest.raises(prc_native.NativePRCError):
        prc_native.parse(b"nope")
    with pytest.raises(prc_native.NativePRCError):
        prc_native.parse(b"BPRC" + struct.pack("<II", 1, 1))
    with pytest.raises(prc_native.NativePRCError):
        prc_native.parse(b"BPRC" + struct.pack("<II", 99, 0))
    with pytest.raises(prc_native.NativePRCError):
        prc_native.parse(_block([]) + b"extra")


def test_prc_falls_back_to_python_reader(monkeypatch):
    data = build_prc([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [(0, 1, 2)])
    monkeypatch.setattr(prc_native, "available", lambda: False)
    assert pdf3d.decode_prc(data).triangle_count == 1

    def broken(data, progress=None):
        raise prc_native.NativePRCError("roto")

    monkeypatch.setattr(prc_native, "available", lambda: True)
    monkeypatch.setattr(prc_native, "decode", broken)
    assert pdf3d.decode_prc(data).triangle_count == 1


@native
def test_native_prc_compressed_tessellation():
    # Dos cubos escritos con teselación comprimida (PRC_TYPE_TESS_3D_Compressed),
    # que el lector en Python no sabe descomprimir.
    scene = prc_native.decode_file(str(DATA / "compressed.prc"))
    assert [part.name for part in scene.parts] == ["Base", "Columna"]
    assert scene.triangle_count == 24
    column = scene.parts[1]
    assert column.matrix[12:15] == pytest.approx((0.5, 0.5, 1.0))
    primitive = scene.primitives[column.primitives[0]]
    assert scene.materials[primitive.material].diffuse == pytest.approx((0.1, 0.3, 0.9))
    # 18 aristas (12 del cubo y 6 diagonales), de las que solo las 12 del cubo son vivas.
    assert len(primitive.wire) // 24 == 18
    assert len(primitive.edges) // 24 == 12
    lo, hi = scene.bounds()
    assert lo == pytest.approx((0.0, 0.0, 0.0), abs=1e-4)
    assert hi == pytest.approx((2.0, 2.0, 2.0), abs=1e-4)


@native
def test_native_prc_matches_python_reader():
    positions = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]
    data = build_prc(positions, [(0, 1, 2), (0, 2, 3)], color=(0.2, 0.6, 0.3), translate=(3.0, 1.0, 0.0))
    native_scene = prc_native.decode(data)
    python_scene = prc.decode(data)
    assert native_scene.triangle_count == python_scene.triangle_count == 2
    for (a_lo, a_hi), (b_lo, b_hi) in [(native_scene.bounds(), python_scene.bounds())]:
        assert a_lo == pytest.approx(b_lo)
        assert a_hi == pytest.approx(b_hi)
    material = native_scene.materials[native_scene.primitives[0].material]
    assert material.diffuse == pytest.approx((0.2, 0.6, 0.3))


@native
def test_native_prc_rejects_other_data(tmp_path):
    bad = tmp_path / "bad.prc"
    bad.write_bytes(b"PRC" + bytes(40))
    with pytest.raises(prc_native.NativePRCError):
        prc_native.decode_file(str(bad))


@native
def test_load_compressed_prc_in_subprocess():
    from blackpdf.procworker import run_blocking

    scene = run_blocking("blackpdf.core.model3d:load_file", str(DATA / "compressed.prc"))
    assert scene.format == "PRC" and scene.triangle_count == 24


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
