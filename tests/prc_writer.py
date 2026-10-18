"""Escritor PRC mínimo para los tests del lector.

Genera archivos con una estructura, una pieza teselada, un color y una
ocurrencia con traslación. Sigue el formato de ISO 14739-1; los doubles se
codifican con la tabla de valores frecuentes, como los exportadores reales.
"""

from __future__ import annotations

import struct
import zlib

from blackpdf.core.model3d.prc_doubles import TABLE

_EXPONENT_CODES = {}
_EXACT_CODES = {}
for _is_exp, _length, _bits, _upper, _lower in TABLE:
    if _is_exp:
        _EXPONENT_CODES.setdefault((_upper >> 20) & 0x7FF, (_length, _bits))
    else:
        _EXACT_CODES.setdefault((_upper, _lower), (_length, _bits))


class BitWriter:
    def __init__(self):
        self.bits: list[int] = []
        self.name = ""
        self.graphics = (-1, -1, 1)

    def bit(self, value) -> None:
        self.bits.append(1 if value else 0)

    def value(self, number: int, count: int) -> None:
        for shift in range(count - 1, -1, -1):
            self.bits.append((number >> shift) & 1)

    def char(self, number: int) -> None:
        self.value(number & 0xFF, 8)

    def uint(self, number: int) -> None:
        while number:
            self.bit(1)
            self.char(number & 0xFF)
            number >>= 8
        self.bit(0)

    def int(self, number: int) -> None:
        if number == 0:
            self.bit(0)
            return
        while True:
            low = number & 0xFF
            self.bit(1)
            self.char(low)
            number >>= 8
            if (number == 0 and not low & 0x80) or (number == -1 and low & 0x80):
                self.bit(0)
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
        lower, upper = struct.unpack("<II", struct.pack("<d", number))
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
        raw = struct.pack("<d", number)
        if (upper & 0xFFFFF) == 0 and lower == 0:
            self.bit(0)
            return
        self.bit(1)
        self.value(raw[6] & 0x0F, 4)
        for index in range(5, -1, -1):
            self.bit(1)
            self.char(raw[index])

    def tobytes(self) -> bytes:
        bits = self.bits + [0] * (-len(self.bits) % 8)
        out = bytearray()
        for index in range(0, len(bits), 8):
            byte = 0
            for bit in bits[index:index + 8]:
                byte = (byte << 1) | bit
            out.append(byte)
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


def build_prc(positions, triangles, color=(0.8, 0.2, 0.1), translate=(10.0, 0.0, 0.0), normals=None) -> bytes:
    """PRC con una pieza (una cara de triángulos), un color y una traslación."""
    uuid = (0x33595341, 1149770900, 7, 0xA5A55A5A)

    g = BitWriter()
    g.uint(0)  # schema
    g.uint(303)
    g.content()
    g.uint(0)  # estructuras referenciadas
    g.double(0.0)
    g.double(40.0)
    g.string("")
    g.uint(0)  # fuentes
    g.uint(1)
    for component in color:
        g.double(component)
    g.uint(0)  # imágenes
    g.uint(0)  # texturas
    g.uint(0)  # materiales
    g.uint(0)  # patrones de línea
    g.uint(1)  # estilos
    g.uint(701)
    g.content(referenceable=True)
    g.double(0.0)
    g.bit(0)
    g.uint(0)
    g.bit(0)
    g.uint(1)  # color 0 + 1
    g.bit(0)
    g.bit(0)
    g.bit(0)
    g.bit(0)
    g.uint(0)  # patrones de relleno
    g.uint(0)  # sistemas de coordenadas
    g.uint(0)  # datos de usuario

    t = BitWriter()
    t.uint(304)
    t.content()
    t.uint(1)
    t.uint(311)
    t.content("Pieza A", referenceable=True)
    t.graphics_content(style=0)
    for value in (0.0, 0.0, 0.0, 1.0, 1.0, 1.0):
        t.double(value)
    t.uint(1)
    t.uint(237)  # PolyBrepModel
    t.content("Cuerpo", referenceable=True)
    t.graphics_content(style=0)
    t.uint(0)  # sin sistema de coordenadas
    t.uint(1)  # teselación 0
    t.bit(1)  # cerrado
    t.uint(0)
    for _ in range(4):
        t.uint(0)  # marcas
    t.uint(0)  # vistas
    t.uint(0)
    t.uint(1)
    t.uint(310)
    t.content("Conjunto", referenceable=True)
    t.graphics_content(style=-1)
    t.uint(1)  # pieza 0
    t.uint(0)
    t.uint(0)
    t.uint(0)  # hijos
    t.char(0)
    t.bit(1)
    t.double(1.0)
    t.char(0)
    t.int(0)
    t.bit(1)  # ubicación
    t.uint(202)
    t.char(0x01)
    for value in translate:
        t.double(value)
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

    s = BitWriter()
    s.uint(305)
    s.content()
    s.uint(1)
    s.uint(172)
    s.bit(0)
    flat = [c for p in positions for c in p]
    s.uint(len(flat))
    for value in flat:
        s.double(value)
    s.bit(1)
    s.bit(0)
    s.bit(normals is None)
    if normals is None:
        s.char(0)
        s.double(40.0)
        s.uint(0)
    else:
        flat_normals = [c for n in normals for c in n]
        s.uint(len(flat_normals))
        for value in flat_normals:
            s.double(value)
    s.uint(0)  # aristas
    indices = []
    for tri in triangles:
        for vertex in tri:
            if normals is not None:
                indices.append(vertex * 3)
            indices.append(vertex * 3)
    s.uint(len(indices))
    for index in indices:
        s.uint(index)
    s.uint(1)
    s.uint(174)
    s.uint(0)
    s.uint(0)
    s.uint(0)
    s.uint(0x0002)
    s.uint(0)
    s.uint(1)
    s.uint(len(triangles))
    s.uint(0)
    s.bit(0)
    s.uint(0)  # coordenadas de textura
    s.uint(0)

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

    m = BitWriter()
    m.uint(0)
    m.uint(301)
    m.content("PRC")
    m.bit(1)
    m.double(1.0)
    m.uint(1)
    for value in uuid:
        m.uint(value)
    m.uint(1)
    m.bit(1)
    m.uint(0)
    m.uint(0)

    def uint32(value):
        return struct.pack("<I", value)

    def uid():
        return b"".join(uint32(v) for v in uuid)

    fs_header = b"PRC" + uint32(8137) + uint32(8137) + uid() + bytes(16) + uint32(0)
    sections = [_section(g), _section(t), _section(s), _section(geo), _section(extra)]
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
