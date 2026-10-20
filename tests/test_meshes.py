"""Lectores de piezas 3D: STL, OBJ, 3MF, glTF/GLB, STEP e IGES (archivos creados en cada test)."""

from __future__ import annotations

import base64
import json
import struct
import zipfile

import pytest

from blackpdf.core.errors import Model3DEmptyError, Model3DError
from blackpdf.core.model3d import meshes

CUBE = [(0, 0, 0), (10, 0, 0), (10, 10, 0), (0, 10, 0), (0, 0, 10), (10, 0, 10), (10, 10, 10), (0, 10, 10)]
QUADS = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
TRIS = [(q[0], q[1], q[2]) for q in QUADS] + [(q[0], q[2], q[3]) for q in QUADS]


def _box(parts):
    xs = [p[0] for part in parts for p in part.positions]
    zs = [p[2] for part in parts for p in part.positions]
    return min(xs), max(xs), min(zs), max(zs)


def test_stl_binary_and_ascii(tmp_path):
    binary = tmp_path / "cubo.stl"
    data = bytearray(b"\0" * 80 + struct.pack("<I", len(TRIS)))
    for tri in TRIS:
        data += struct.pack("<12fH", 0, 0, 0, *CUBE[tri[0]], *CUBE[tri[1]], *CUBE[tri[2]], 0)
    binary.write_bytes(bytes(data))
    (part,) = meshes.read_parts(str(binary))
    assert len(part.triangles) == 12 and len(part.positions) == 8, "vértices repetidos unidos"

    ascii_file = tmp_path / "dos.stl"
    lines = []
    for name in ("base", "tapa"):
        lines.append(f"solid {name}")
        for tri in TRIS:
            lines += ["facet normal 0 0 0", "outer loop", *(f"vertex {x} {y} {z}" for x, y, z in (CUBE[i] for i in tri)),
                      "endloop", "endfacet"]
        lines.append(f"endsolid {name}")
    ascii_file.write_text("\n".join(lines))
    parts = meshes.read_parts(str(ascii_file))
    assert [p.name for p in parts] == ["base", "tapa"] and all(len(p.triangles) == 12 for p in parts)


def test_obj_groups_and_materials(tmp_path):
    (tmp_path / "piezas.mtl").write_text("newmtl rojo\nKd 0.9 0.1 0.1\nnewmtl azul\nKd 0.1 0.2 0.9\n")
    lines = ["mtllib piezas.mtl", *(f"v {x} {y} {z}" for x, y, z in CUBE), "g Carcasa", "usemtl rojo"]
    lines += [f"f {' '.join(str(i + 1) for i in quad)}" for quad in QUADS[:3]]
    lines += ["g Tapa", "usemtl azul"] + [f"f {a + 1}/1/1 {b + 1}/1/1 {c + 1}/1/1" for a, b, c in TRIS[3:6]]
    obj = tmp_path / "piezas.obj"
    obj.write_text("\n".join(lines))
    parts = meshes.read_parts(str(obj))
    assert [(p.name, len(p.triangles), p.color) for p in parts] == [
        ("Carcasa", 6, (0.9, 0.1, 0.1)), ("Tapa", 3, (0.1, 0.2, 0.9))]


def test_3mf_objects_components_and_colors(tmp_path):
    vertices = "".join(f'<vertex x="{x}" y="{y}" z="{z}"/>' for x, y, z in CUBE)
    triangles = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in TRIS)
    model = f"""<?xml version="1.0"?>
<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">
 <resources>
  <basematerials id="5"><base name="Rojo" displaycolor="#E5484DFF"/></basematerials>
  <object id="1" type="model" name="Bloque" pid="5" pindex="0"><mesh><vertices>{vertices}</vertices>
   <triangles>{triangles}</triangles></mesh></object>
  <object id="2" type="model" name="Pareja"><components>
   <component objectid="1"/><component objectid="1" transform="1 0 0 0 1 0 0 0 1 20 0 0"/>
  </components></object>
 </resources>
 <build><item objectid="2" transform="1 0 0 0 1 0 0 0 1 0 0 5"/></build>
</model>"""
    path = tmp_path / "pareja.3mf"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("3D/3dmodel.model", model)
    parts = meshes.read_parts(str(path))
    assert [p.name for p in parts] == ["Bloque", "Bloque (2)"]
    assert parts[0].color == pytest.approx((229 / 255, 72 / 255, 77 / 255))
    assert _box(parts) == (0, 30, 5, 15), "componentes y elemento colocados con su transformación"


def _gltf_document(buffer_uri: str | None, data: bytes) -> dict:
    return {
        "asset": {"version": "2.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"name": "Raíz", "children": [1]}, {"name": "Cubo", "mesh": 0, "translation": [0, 0, 0]}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0}, "indices": 1, "material": 0}]}],
        "materials": [{"pbrMetallicRoughness": {"baseColorFactor": [0.2, 0.6, 0.3, 1]}}],
        "buffers": [{"byteLength": len(data), **({"uri": buffer_uri} if buffer_uri else {})}],
        "bufferViews": [{"buffer": 0, "byteOffset": 0, "byteLength": 96},
                        {"buffer": 0, "byteOffset": 96, "byteLength": len(data) - 96}],
        "accessors": [{"bufferView": 0, "componentType": 5126, "count": 8, "type": "VEC3"},
                      {"bufferView": 1, "componentType": 5123, "count": 36, "type": "SCALAR"}],
    }


def test_gltf_and_glb_are_rotated_to_z_up(tmp_path):
    data = b"".join(struct.pack("<3f", *p) for p in CUBE) + struct.pack("<36H", *[i for t in TRIS for i in t])
    gltf = tmp_path / "cubo.gltf"
    uri = "data:application/octet-stream;base64," + base64.b64encode(data).decode()
    gltf.write_text(json.dumps(_gltf_document(uri, data)))
    (part,) = meshes.read_parts(str(gltf))
    assert part.name == "Cubo" and part.color == (0.2, 0.6, 0.3) and len(part.triangles) == 12
    ys = [p[1] for p in part.positions]
    assert min(ys) == -10 and max(ys) == 0, "glTF Y arriba pasa a Z arriba"

    payload = json.dumps(_gltf_document(None, data)).encode()
    payload += b" " * (-len(payload) % 4)
    glb = tmp_path / "cubo.glb"
    body = struct.pack("<II", len(payload), 0x4E4F534A) + payload + struct.pack("<II", len(data), 0x004E4942) + data
    glb.write_bytes(b"glTF" + struct.pack("<II", 2, 12 + len(body)) + body)
    assert len(meshes.read_parts(str(glb))[0].triangles) == 12


def test_errors(tmp_path):
    empty = tmp_path / "vacio.obj"
    empty.write_text("# nada\n")
    with pytest.raises(Model3DEmptyError):
        meshes.read_parts(str(empty))
    broken = tmp_path / "roto.stl"
    broken.write_bytes(b"\x01\x02 basura")
    with pytest.raises(Model3DError):
        meshes.read_parts(str(broken))


@pytest.mark.skipif(not meshes.cad_available(), reason="gmsh no está instalado")
def test_step_solids_become_parts(tmp_path):
    import gmsh

    path = tmp_path / "conjunto.step"
    gmsh.initialize(interruptible=False)
    gmsh.option.setNumber("General.Terminal", 0)
    gmsh.model.occ.addBox(0, 0, 0, 40, 20, 10)
    gmsh.model.occ.addCylinder(60, 10, 0, 0, 0, 25, 6)
    gmsh.model.occ.synchronize()
    gmsh.write(str(path))
    gmsh.finalize()
    steps = []
    parts = meshes.read_parts(str(path), progress=lambda done, total: steps.append(done))
    assert len(parts) == 2 and all(len(p.triangles) > 10 for p in parts)
    lo_x, hi_x, lo_z, hi_z = _box(parts)
    assert lo_x == pytest.approx(0, abs=1e-6) and hi_x == pytest.approx(66, abs=1e-6)
    assert hi_z == pytest.approx(25, abs=1e-6)
    assert steps[-1] == 3
    scene = meshes.parts_scene(parts)
    assert scene.triangle_count == sum(len(p.triangles) for p in parts) and len(scene.parts) == 2
