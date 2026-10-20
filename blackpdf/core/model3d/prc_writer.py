"""Escritor de PRC (ISO 14739-1) para piezas de triángulos.

Escribe una pieza por cada malla, con su nombre y su color, como lo hace Asymptote
(writePRC.cc), que es lo que leen Acrobat y otros visores de PDF 3D. Los doubles se
codifican con la tabla de valores frecuentes del formato.
"""

from __future__ import annotations

import math
import struct
import zlib

from .meshes import DEFAULT_COLOR, MeshPart
from .prc_doubles import TABLE

_EXPONENT_CODES: dict[int, tuple[int, int]] = {}
_EXACT_CODES: dict[tuple[int, int], tuple[int, int]] = {}
for _is_exp, _length, _bits, _upper, _lower in TABLE:
    if _is_exp:
        _EXPONENT_CODES.setdefault((_upper >> 20) & 0x7FF, (_length, _bits))
    else:
        _EXACT_CODES.setdefault((_upper, _lower), (_length, _bits))

UUID = (0x33595341, 1149770900, 7, 0xA5A55A5A)
_DOUBLE = struct.Struct("<d")
_HALVES = struct.Struct("<II")


class BitWriter:
    """Flujo de bits del PRC: guarda los bits en un entero y los vuelca por bytes."""

    __slots__ = ("_out", "_acc", "_count", "name", "graphics")

    def __init__(self):
        self._out = bytearray()
        self._acc = 0
        self._count = 0
        self.name = ""
        self.graphics = (-1, -1, 1)

    def value(self, number: int, count: int) -> None:
        self._acc = (self._acc << count) | (number & ((1 << count) - 1))
        self._count += count
        if self._count >= 256:
            spare = self._count & 7
            self._out += (self._acc >> spare).to_bytes((self._count - spare) // 8, "big")
            self._acc &= (1 << spare) - 1
            self._count = spare

    def bit(self, value) -> None:
        self.value(1 if value else 0, 1)

    def char(self, number: int) -> None:
        self.value(number & 0xFF, 8)

    def uint(self, number: int) -> None:
        while number:
            self.value(0x100 | (number & 0xFF), 9)
            number >>= 8
        self.value(0, 1)

    def int(self, number: int) -> None:
        if number == 0:
            self.value(0, 1)
            return
        while True:
            low = number & 0xFF
            self.value(0x100 | low, 9)
            number >>= 8
            if (number == 0 and not low & 0x80) or (number == -1 and low & 0x80):
                self.value(0, 1)
                return

    def string(self, text: str | None) -> None:
        if text is None:
            self.bit(0)
            return
        raw = text.encode("utf-8")
        self.bit(1)
        self.uint(len(raw))
        for byte in raw:
            self.char(byte)

    def double(self, number: float) -> None:
        raw = _DOUBLE.pack(number)
        lower, upper = _HALVES.unpack(raw)
        positive_upper = upper & 0x7FFFFFFF
        if positive_upper == 0 and lower == 0:
            self.value(0b01, 2)
            return
        exact = _EXACT_CODES.get((positive_upper, lower))
        if exact is not None:
            self.value(exact[1], exact[0])
            self.bit(upper >> 31)
            return
        length, code = _EXPONENT_CODES[(upper >> 20) & 0x7FF]
        self.value(code, length)
        self.bit(upper >> 31)
        if (upper & 0xFFFFF) == 0 and lower == 0:
            self.bit(0)
            return
        self.value(0x10 | (raw[6] & 0x0F), 5)
        for index in range(5, -1, -1):
            self.value(0x100 | raw[index], 9)

    def doubles(self, *values: float) -> None:
        for value in values:
            self.double(value)

    def tobytes(self) -> bytes:
        out = bytearray(self._out)
        if self._count:
            pad = -self._count % 8
            out += (self._acc << pad).to_bytes((self._count + pad) // 8, "big")
        return bytes(out)

    # -- contenido común --------------------------------------------------------

    def content(self, name: str = "", referenceable: bool = False) -> None:
        self.uint(0)  # atributos
        if name == self.name:
            self.bit(1)
        else:
            self.bit(0)
            self.string(name)
            self.name = name
        if referenceable:
            self.uint(0)
            self.uint(0)
            self.uint(1)

    def graphics_content(self, style: int = -1, behaviour: int = 1) -> None:
        if self.graphics == (-1, style, behaviour):
            self.bit(1)
            return
        self.bit(0)
        self.uint(0)
        self.uint(style + 1)
        self.char(behaviour & 0xFF)
        self.char(behaviour >> 8)
        self.graphics = (-1, style, behaviour)


def _section(writer: BitWriter) -> bytes:
    return zlib.compress(writer.tobytes())


def _globals(colors: list[tuple[float, float, float]]) -> BitWriter:
    """Globales: un color RGB y un estilo de color por cada pieza."""
    g = BitWriter()
    g.uint(0)  # schema
    g.uint(303)
    g.content()
    g.uint(0)  # estructuras referenciadas
    g.double(0.0)
    g.double(40.0)
    g.string("")
    g.uint(0)  # fuentes
    g.uint(len(colors))
    for color in colors:
        g.doubles(*color)
    g.uint(0)  # imágenes
    g.uint(0)  # texturas
    g.uint(0)  # materiales
    g.uint(0)  # patrones de línea
    g.uint(len(colors))  # estilos
    for index in range(len(colors)):
        g.uint(701)
        g.content(referenceable=True)
        g.double(0.0)
        g.bit(0)
        g.uint(0)
        g.bit(0)
        g.uint(3 * index + 1)  # índice en el array de doubles de los colores, + 1
        g.bit(0)
        g.bit(0)
        g.bit(0)
        g.bit(0)
    g.uint(0)  # patrones de relleno
    g.uint(0)  # sistemas de coordenadas
    g.uint(0)  # datos de usuario
    return g


def _tree(names: list[str], title: str, bounds) -> BitWriter:
    """Árbol: una definición de pieza con un cuerpo por malla, dentro de una ocurrencia."""
    t = BitWriter()
    t.uint(304)
    t.content()
    t.uint(1)
    t.uint(311)
    t.content(title, referenceable=True)
    t.graphics_content(style=-1)
    (x0, y0, z0), (x1, y1, z1) = bounds
    t.doubles(x0, y0, z0, x1, y1, z1)
    t.uint(len(names))
    for index, name in enumerate(names):
        t.uint(237)  # PolyBrepModel
        t.content(name, referenceable=True)
        t.graphics_content(style=index)
        t.uint(0)  # sin sistema de coordenadas
        t.uint(index + 1)  # teselación
        t.bit(0)  # abierto: nadie oculta caras aunque alguna esté al revés
        t.uint(0)  # datos de usuario del elemento
    for _ in range(4):
        t.uint(0)  # marcas
    t.uint(0)  # vistas
    t.uint(0)
    t.uint(1)
    t.uint(310)
    t.content(title, referenceable=True)
    t.graphics_content(style=-1)
    t.uint(1)  # pieza 0
    t.uint(0)
    t.uint(0)
    t.uint(0)  # hijos
    t.char(0)
    t.bit(1)
    t.double(1.0)  # unidades: milímetros
    t.char(0)
    t.int(0)
    t.bit(0)  # sin ubicación
    t.uint(0)  # referencias
    for _ in range(4):
        t.uint(0)
    t.uint(0)  # vistas
    t.bit(0)
    t.uint(0)
    t.uint(0)
    t.uint(0)
    t.uint(302)
    t.content()
    t.uint(2)
    t.uint(1)  # raíz 0 + 1
    t.uint(0)
    return t


def _tessellations(parts: list[MeshPart], progress=None) -> BitWriter:
    """Una teselación 3D por pieza; las normales las calcula el visor (pliegue de 40°)."""
    s = BitWriter()
    s.uint(305)
    s.content()
    s.uint(len(parts))
    total = sum(len(p.triangles) for p in parts) or 1
    done = 0
    for part in parts:
        s.uint(172)
        s.bit(0)
        s.uint(len(part.positions) * 3)
        for x, y, z in part.positions:
            s.double(x)
            s.double(y)
            s.double(z)
        s.bit(1)
        s.bit(0)
        s.bit(1)  # el visor recalcula las normales
        s.char(0)
        s.double(40.0)
        s.uint(0)  # normales
        s.uint(0)  # aristas
        s.uint(len(part.triangles) * 3)
        for count, (a, b, c) in enumerate(part.triangles, 1):
            s.uint(a * 3)
            s.uint(b * 3)
            s.uint(c * 3)
            if progress is not None and count % 20000 == 0:
                progress(done + count, total)
        done += len(part.triangles)
        s.uint(1)
        s.uint(174)
        s.uint(0)
        s.uint(0)
        s.uint(0)
        s.uint(0x0002)
        s.uint(0)
        s.uint(1)
        s.uint(len(part.triangles))
        s.uint(0)
        s.bit(0)
        s.uint(0)  # coordenadas de textura
    s.uint(0)
    return s


def _empty_geometry() -> tuple[BitWriter, BitWriter]:
    geo = BitWriter()
    geo.uint(306)
    geo.content()
    geo.uint(0)
    geo.uint(0)
    extra = BitWriter()
    extra.uint(307)
    extra.content()
    extra.uint(0)
    extra.uint(0)
    return geo, extra


def _assemble(globals_section, tree, tessellation, geometry, extra) -> bytes:
    m = BitWriter()
    m.uint(0)
    m.uint(301)
    m.content("PRC")
    m.bit(1)
    m.double(1.0)
    m.uint(1)
    for value in UUID:
        m.uint(value)
    m.uint(1)
    m.bit(1)
    m.uint(0)
    m.uint(0)

    def uint32(value):
        return struct.pack("<I", value)

    def uid():
        return b"".join(uint32(v) for v in UUID)

    fs_header = b"PRC" + uint32(8137) + uint32(8137) + uid() + bytes(16) + uint32(0)
    sections = [_section(s) for s in (globals_section, tree, tessellation, geometry, extra)]
    header_size = 3 + 8 + 32 + 4 + (16 + 4 + 4 + 6 * 4) + 4 + 4 + 4
    offsets = [header_size]
    position = header_size + len(fs_header)
    for section in sections:
        offsets.append(position)
        position += len(section)
    model = _section(m)
    model_offset = position
    total = model_offset + len(model)
    header = b"PRC" + uint32(8137) + uint32(8137) + uid() + bytes(16) + uint32(1)
    header += uid() + uint32(0) + uint32(6) + b"".join(uint32(o) for o in offsets)
    header += uint32(model_offset) + uint32(total) + uint32(0)
    assert len(header) == header_size
    return header + fs_header + b"".join(sections) + model


def bounds(parts: list[MeshPart]):
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for part in parts:
        for point in part.positions:
            for axis in range(3):
                if point[axis] < lo[axis]:
                    lo[axis] = point[axis]
                if point[axis] > hi[axis]:
                    hi[axis] = point[axis]
    if lo[0] == math.inf:
        return (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)
    return tuple(lo), tuple(hi)


def write_prc(parts: list[MeshPart], title: str = "Modelo", progress=None) -> bytes:
    """Archivo PRC con una pieza por malla (nombre y color de cada una)."""
    colors = [tuple(float(c) for c in (part.color or DEFAULT_COLOR)) for part in parts]
    geometry, extra = _empty_geometry()
    return _assemble(_globals(colors), _tree([p.name for p in parts], title, bounds(parts)),
                     _tessellations(parts, progress), geometry, extra)
