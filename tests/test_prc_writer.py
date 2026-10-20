"""Escritor de PRC: mismo flujo de bits que el de los tests del lector y se lee de vuelta."""

from __future__ import annotations

import random

import pytest

from blackpdf.core.model3d import prc_writer
from blackpdf.core.model3d.meshes import MeshPart
from blackpdf.core.model3d.pdf3d import decode

from .prc_writer import BitWriter as ReferenceWriter


def test_fast_bitwriter_matches_reference():
    rng = random.Random(7)
    fast, reference = prc_writer.BitWriter(), ReferenceWriter()
    for _ in range(4000):
        kind = rng.choice(("uint", "int", "double", "bit", "char", "string"))
        if kind == "uint":
            value = rng.choice((0, 1, 255, 256, rng.randrange(1 << 40)))
        elif kind == "int":
            value = rng.choice((0, -1, 127, -128, rng.randrange(-(1 << 30), 1 << 30)))
        elif kind == "double":
            value = rng.choice((0.0, 1.0, -0.5, 40.0, rng.uniform(-1e6, 1e6), rng.uniform(-1e-6, 1e-6)))
        elif kind == "bit":
            value = rng.random() < 0.5
        elif kind == "char":
            value = rng.randrange(256)
        else:
            value = rng.choice(("", "Pieza", "Sólido 1"))
        getattr(fast, kind)(value)
        getattr(reference, kind)(value)
    assert fast.tobytes() == reference.tobytes()


def _cube(name, offset, color):
    o = offset
    points = [(o, 0, 0), (o + 10, 0, 0), (o + 10, 10, 0), (o, 10, 0),
              (o, 0, 10), (o + 10, 0, 10), (o + 10, 10, 10), (o, 10, 10)]
    quads = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    part = MeshPart(name, points, color=color)
    for quad in quads:
        part.add_polygon(list(quad))
    return part


@pytest.mark.parametrize("native", [True, False])
def test_written_prc_reads_back(native, monkeypatch):
    if not native:
        from blackpdf.core.model3d import prc_native

        monkeypatch.setattr(prc_native, "available", lambda: False)
    parts = [_cube("Base", 0, (0.9, 0.2, 0.2)), _cube("Tapa", 20, (0.2, 0.3, 0.9))]
    steps = []
    data = prc_writer.write_prc(parts, "Conjunto", progress=lambda d, t: steps.append(d))
    assert data[:3] == b"PRC"
    scene = decode(data, "PRC")
    assert scene.triangle_count == 24
    assert sorted(part.name for part in scene.parts) == ["Base", "Tapa"]
    colors = sorted(tuple(round(c, 2) for c in m.diffuse) for m in scene.materials)
    assert (0.2, 0.3, 0.9) in colors and (0.9, 0.2, 0.2) in colors
    (x0, _, _), (x1, _, z1) = scene.bounds()
    assert (x0, x1, z1) == pytest.approx((0, 30, 10))
