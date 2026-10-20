"""Escritor PRC mínimo para los tests del lector.

Genera archivos con una estructura, piezas teseladas o de geometría exacta,
colores y una ocurrencia con traslación. Sigue el formato de ISO 14739-1; los
doubles se codifican con la tabla de valores frecuentes, como los exportadores
reales. La geometría exacta se escribe como lo hace Asymptote (writePRC.cc),
incluida la compresión de los parches de Bézier.
"""

from __future__ import annotations

import math
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

UUID = (0x33595341, 1149770900, 7, 0xA5A55A5A)


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

    def doubles(self, *values: float) -> None:
        for value in values:
            self.double(value)

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


# -- secciones ---------------------------------------------------------------------


def _globals(colors: list[tuple[float, float, float]]) -> BitWriter:
    """Globales: un color RGB y un estilo de color por cada entrada."""
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


def _tree(items, translate) -> BitWriter:
    """Árbol: una pieza con ``items`` (funciones que escriben cada elemento)
    dentro de una ocurrencia con traslación."""
    t = BitWriter()
    t.uint(304)
    t.content()
    t.uint(1)
    t.uint(311)
    t.content("Pieza A", referenceable=True)
    t.graphics_content(style=0)
    t.doubles(0.0, 0.0, 0.0, 1.0, 1.0, 1.0)
    t.uint(len(items))
    for write in items:
        write(t)
        t.uint(0)  # datos de usuario del elemento
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
    t.doubles(*translate)
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


def _poly_brep(name: str = "Cuerpo", style: int = 0):
    def write(t: BitWriter) -> None:
        t.uint(237)  # PolyBrepModel
        t.content(name, referenceable=True)
        t.graphics_content(style=style)
        t.uint(0)  # sin sistema de coordenadas
        t.uint(1)  # teselación 0
        t.bit(1)  # cerrado
    return write


def _tessellation_section(positions, triangles, normals=None) -> BitWriter:
    """Sección de teselación con una teselación 3D de una cara de triángulos."""
    s = BitWriter()
    s.uint(305)
    s.content()
    s.uint(1)
    s.uint(172)
    s.bit(0)
    flat = [c for p in positions for c in p]
    s.uint(len(flat))
    s.doubles(*flat)
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
        s.doubles(*flat_normals)
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
    return s


def build_prc(positions, triangles, color=(0.8, 0.2, 0.1), translate=(10.0, 0.0, 0.0), normals=None) -> bytes:
    """PRC con una pieza (una cara de triángulos), un color y una traslación."""
    geo, extra = _empty_geometry()
    s = _tessellation_section(positions, triangles, normals)
    return _assemble(_globals([color]), _tree([_poly_brep()], translate), s, geo, extra)


# -- geometría exacta, como Asymptote --------------------------------------------------


def _bits_for_uint(value: int) -> int:
    bits, limit = 2, 2
    while value >= limit:
        limit *= 2
        bits += 1
    return bits - 1


def _bits_for_int(value: int) -> int:
    return _bits_for_uint(abs(value)) + 1


def _intdiv(value: float, tolerance: float) -> int:
    ratio = abs(value) / tolerance
    result = int(ratio)
    if ratio - result >= 0.5:
        result += 1
    return -result if value < 0 else result


def _var_int(w: BitWriter, value: int, bits: int) -> None:
    w.bit(value < 0)
    w.value(abs(value), bits - 1)


def _bits_then_uint(w: BitWriter, value: int) -> None:
    bits = _bits_for_uint(value)
    w.value(bits, 5)
    w.value(value, bits)


def _sub(a, b):
    return [x - y for x, y in zip(a, b)]


def _add(*vectors):
    return [sum(values) for values in zip(*vectors)]


def _scale(v, k):
    return [x * k for x in v]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return sum(x * y for x, y in zip(a, b))


def _normalized(v):
    length = math.sqrt(_dot(v, v))
    if length < 1.1920929e-07:
        return None
    return [x / length for x in v]


def _compressed_face(w: BitWriter, degree: int, control_points, tolerance: float) -> None:
    """PRCCompressedFace::serializeCompressedAnaNurbs y serializeCompressedNurbs."""
    w.bit(0)  # no es una curva
    w.value(13, 4)  # PRC_HCG_AnaNurbs
    w.bit(1)  # orientación con la cáscara
    w.bit(0)  # sin recortar
    step = 0.2 * tolerance
    n = degree + 1
    w.value(degree, 5)
    w.value(degree, 5)
    width = (degree + 1).bit_length()
    for _ in range(2):  # nudos en u y en v
        w.value(2, 16)
        w.bit(0)
        w.value(degree + 1, width)
        w.bit(1)
    w.bit(0)
    w.bit(0)
    P = [[list(control_points[i * n + j]) for j in range(n)] for i in range(n)]
    packed = [[None] * n for _ in range(n)]
    kinds = [[0] * n for _ in range(n)]
    iso_bits = rest_bits = 1
    for j in range(1, n):
        packed[0][j] = [_intdiv(d, step) for d in _sub(P[0][j], P[0][j - 1])]
        P[0][j] = _add(P[0][j - 1], _scale(packed[0][j], step))
        iso_bits = max(iso_bits, max(_bits_for_int(v) for v in packed[0][j]))
    for i in range(1, n):
        packed[i][0] = [_intdiv(d, step) for d in _sub(P[i][0], P[i - 1][0])]
        P[i][0] = _add(P[i - 1][0], _scale(packed[i][0], step))
        iso_bits = max(iso_bits, max(_bits_for_int(v) for v in packed[i][0]))
    for i in range(1, n):
        for j in range(1, n):
            packed[i][j] = [0, 0, 0]
            V = _sub(P[i - 1][j], P[i - 1][j - 1])
            U = _sub(P[i][j - 1], P[i - 1][j - 1])
            base = _add(P[i - 1][j - 1], U, V)
            pc = _sub(P[i][j], base)
            if math.sqrt(_dot(pc, pc)) < step:
                kinds[i][j] = 0
                P[i][j] = base
            else:
                ue, ne = _normalized(U), _normalized(_cross(U, V))
                if math.sqrt(_dot(V, V)) < 1.1920929e-07 or ue is None or ne is None:
                    kinds[i][j] = 3
                    packed[i][j] = [_intdiv(c, step) for c in pc]
                    P[i][j] = _add(base, _scale(packed[i][j], step))
                else:
                    nue = _cross(ne, ue)
                    x, y, z = _dot(pc, ue), _dot(pc, nue), _dot(pc, ne)
                    if x * x + y * y < step * step:
                        kinds[i][j] = 1
                        packed[i][j] = [0, 0, _intdiv(z, step)]
                        P[i][j] = _add(base, _scale(ne, packed[i][j][2] * step))
                    elif abs(z) < step / 2:
                        kinds[i][j] = 2
                        packed[i][j] = [_intdiv(x, step), _intdiv(y, step), 0]
                        P[i][j] = _add(base, _scale(ue, packed[i][j][0] * step), _scale(nue, packed[i][j][1] * step))
                    else:
                        kinds[i][j] = 3
                        packed[i][j] = [_intdiv(c, step) for c in pc]
                        P[i][j] = _add(base, _scale(packed[i][j], step))
            rest_bits = max(rest_bits, max(_bits_for_int(v) for v in packed[i][j]))
    if rest_bits == 2:
        rest_bits -= 1
    w.value(iso_bits, 20)
    w.value(rest_bits, 20)
    w.doubles(*P[0][0])
    for j in range(1, n):
        for v in packed[0][j]:
            _var_int(w, v, iso_bits + 1)
    for i in range(1, n):
        for v in packed[i][0]:
            _var_int(w, v, iso_bits + 1)
    for i in range(1, n):
        for j in range(1, n):
            w.value(kinds[i][j], 2)
            values = {0: [], 1: packed[i][j][2:], 2: packed[i][j][:2], 3: packed[i][j]}[kinds[i][j]]
            for v in values:
                _var_int(w, v, rest_bits + 1)
    w.bit(1)  # parametrización uniforme en u
    w.bit(1)  # y en v
    w.bit(0)  # no racional


def compressed_brep(patches, tolerance: float = 0.01):
    """Cuerpo PRC_TYPE_TOPO_BrepDataCompress con parches (grado, 16 o 4 puntos)."""

    def write(w: BitWriter) -> None:
        w.uint(156)
        w.bit(0)  # sin información base
        w.char(0)
        w.double(tolerance)
        _bits_then_uint(w, 1)
        w.value(0, 1)
        w.value(0, 1)
        w.bit(1)  # una cáscara y un conexo
        w.bit(len(patches) == 1)
        if len(patches) != 1:
            _bits_then_uint(w, len(patches))
        for degree, points in patches:
            _compressed_face(w, degree, points, tolerance)
        for _ in patches:
            w.bit(0)  # no es cara iso
        for _ in patches:
            w.bit(0)  # sin información base
    write.compressed = True
    write.tolerance = tolerance
    write.faces = len(patches)
    return write


def nurbs_surface(degree_u, degree_v, rows, knots_u, knots_v):
    def write(w: BitWriter) -> None:
        w.uint(80)  # PRC_TYPE_SURF_NURBS
        w.bit(0)
        w.uint(0)
        w.bit(0)  # no racional
        w.uint(degree_u)
        w.uint(degree_v)
        w.uint(len(rows) - 1)
        w.uint(len(rows[0]) - 1)
        w.uint(len(knots_u) - 1)
        w.uint(len(knots_v) - 1)
        for row in rows:
            for point in row:
                w.doubles(*point)
        w.doubles(*knots_u)
        w.doubles(*knots_v)
        w.uint(0)
        w.uint(0)
    return write


def _placement(w: BitWriter, origin=(0.0, 0.0, 0.0), x_axis=None, y_axis=None) -> None:
    """Transformación de una superficie analítica: traslación y, si se da, giro (ejes x e y)."""
    behaviour = (0x01 if any(origin) else 0) | (0x02 if x_axis is not None else 0)
    if not behaviour:
        w.bit(0)
        return
    w.bit(1)
    w.char(behaviour)
    if behaviour & 0x01:
        w.doubles(*origin)
    if behaviour & 0x02:
        w.doubles(*x_axis)
        w.doubles(*y_axis)


def cylinder_surface(radius: float, height: float, origin=(0.0, 0.0, 0.0), x_axis=None, y_axis=None):
    """Cilindro sin tapas, de eje z local (addCylinder de Asymptote)."""

    def write(w: BitWriter) -> None:
        w.uint(82)  # PRC_TYPE_SURF_Cylinder
        w.bit(0)
        w.uint(0)
        _placement(w, origin, x_axis, y_axis)
        w.bit(0)
        w.doubles(0.0, 0.0, 2 * math.pi, height)
        w.doubles(1.0, 1.0, 0.0, 0.0)
        w.double(radius)
    return write


def torus_surface(major: float, minor: float, origin=(0.0, 0.0, 0.0), x_axis=None, y_axis=None):
    """Toro completo de eje z local (addTorus de Asymptote)."""

    def write(w: BitWriter) -> None:
        w.uint(92)  # PRC_TYPE_SURF_Torus
        w.bit(0)
        w.uint(0)
        _placement(w, origin, x_axis, y_axis)
        w.bit(0)
        w.doubles(0.0, 0.0, 2 * math.pi, 2 * math.pi)
        w.doubles(1.0, 1.0, 0.0, 0.0)
        w.double(major)
        w.double(minor)
    return write


def sphere_surface(radius: float, origin=(0.0, 0.0, 0.0)):
    def write(w: BitWriter) -> None:
        w.uint(88)  # PRC_TYPE_SURF_Sphere
        w.bit(0)
        w.uint(0)
        w.bit(1)  # transformación: traslación
        w.char(0x01)
        w.doubles(*origin)
        w.bit(0)  # sin intercambio uv
        w.doubles(0.0, -0.5 * math.pi, 2 * math.pi, 0.5 * math.pi)
        w.doubles(1.0, 1.0, 0.0, 0.0)
        w.double(radius)
    return write


def disk_surface(radius: float, origin=(0.0, 0.0, 0.0), x_axis=None, y_axis=None):
    """Disco como superficie reglada entre dos círculos (addDisk de Asymptote)."""

    def circle(w: BitWriter, r: float) -> None:
        w.bit(0)
        w.uint(14)  # PRC_TYPE_CRV_Circle
        w.bit(0)
        w.uint(0)
        w.bit(1)  # 3D
        w.bit(0)  # sin transformación
        w.doubles(0.0, 2 * math.pi, 1.0, 0.0)
        w.double(r)

    def write(w: BitWriter) -> None:
        w.uint(87)  # PRC_TYPE_SURF_Ruled
        w.bit(0)
        w.uint(0)
        _placement(w, origin, x_axis, y_axis)
        w.bit(0)
        w.doubles(0.0, 0.0, 1.0, 2 * math.pi)
        w.doubles(1.0, -1.0, 0.0, 2 * math.pi)
        circle(w, radius)
        circle(w, 0.0)
    return write


def brep(surfaces):
    """Cuerpo PRC_TYPE_TOPO_BrepData: un conexo, una cáscara, una cara por superficie."""

    def write(w: BitWriter) -> None:
        w.uint(154)
        w.bit(0)
        w.char(0)
        w.uint(1)
        w.bit(0)
        w.uint(151)  # conexo
        w.bit(0)
        w.uint(1)
        w.bit(0)
        w.uint(150)  # cáscara
        w.bit(0)
        w.bit(0)
        w.uint(len(surfaces))
        for surface in surfaces:
            w.bit(0)
            w.uint(149)  # cara
            w.bit(0)
            w.bit(0)
            surface(w)
            w.bit(0)  # sin dominio recortado
            w.bit(0)  # sin tolerancia
            w.uint(0)  # contornos
            w.int(-1)
            w.char(1)
    write.compressed = False
    write.faces = len(surfaces)
    return write


def bezier_wire(points):
    """Cuerpo de alambre con una curva NURBS cúbica (addBezierCurve de Asymptote)."""
    count = len(points)
    knots = [1.0] + [float((i + 2) // 3) for i in range(1, 3 + count)] + [float((3 + count + 1) // 3)]

    def write(w: BitWriter) -> None:
        w.uint(153)  # cuerpo de un alambre
        w.bit(0)
        w.char(0)
        w.bit(0)
        w.uint(145)  # arista de alambre
        w.bit(0)
        w.bit(0)
        w.uint(13)  # PRC_TYPE_CRV_NURBS
        w.bit(0)
        w.uint(0)
        w.bit(1)  # 3D
        w.bit(0)  # no racional
        w.uint(3)
        w.uint(count - 1)
        w.uint(len(knots) - 1)
        for point in points:
            w.doubles(*point)
        w.doubles(*knots)
        w.uint(0)
        w.uint(0)
        w.bit(0)  # sin intervalo
    write.compressed = False
    write.faces = 0
    return write


def build_brep_prc(bodies, items, colors, face_styles=None, translate=(0.0, 0.0, 0.0), mesh=None) -> bytes:
    """PRC con geometría exacta en un contexto (y, si se da, una malla teselada).

    ``items`` son tuplas (tipo, nombre, estilo, cuerpo): tipo "brep" o
    "curve". ``face_styles`` da, si se quiere, el estilo de cada cara en el
    orden en que aparecen (geometría extra). ``mesh`` = (posiciones,
    triángulos) añade una pieza teselada «Malla» con el estilo 0.
    """

    def item(kind, name, style, body):
        def write(t: BitWriter) -> None:
            t.uint(232 if kind == "brep" else 233)
            t.content(name, referenceable=True)
            t.graphics_content(style=style)
            t.uint(0)  # sin sistema de coordenadas
            t.uint(0)  # sin teselación
            t.bit(1)
            t.uint(1)  # contexto 0 + 1
            t.uint(body + 1)
            if kind == "brep":
                t.bit(0)  # no cerrado
        return write

    if mesh is None:
        s = BitWriter()
        s.uint(305)
        s.content()
        s.uint(0)
        s.uint(0)
    else:
        s = _tessellation_section(*mesh)

    geo = BitWriter()
    geo.uint(306)
    geo.content()
    geo.uint(1)
    geo.uint(141)  # contexto topológico
    geo.content()
    geo.char(0)
    geo.double(0.0)
    geo.double(0.0)
    geo.bit(0)
    geo.bit(0)
    geo.uint(len(bodies))
    for body in bodies:
        body(geo)
    geo.uint(0)

    extra = BitWriter()
    extra.uint(307)
    extra.content()
    extra.uint(1)
    extra.uint(len(bodies))
    for body in bodies:
        extra.uint(156 if body.compressed else 154)
        if body.compressed:
            extra.double(body.tolerance)
    extra.graphics = (-1, -1, 1)
    if face_styles:
        extra.uint(1)
        extra.uint(149)
        extra.uint(len(face_styles))
        for style in face_styles:
            extra.bit(style is not None)
            if style is not None:
                extra.graphics_content(style=style)
    else:
        extra.uint(0)
    extra.uint(0)

    writers = [item(*spec) for spec in items]
    if mesh is not None:
        writers.insert(0, _poly_brep("Malla", 0))
    tree = _tree(writers, translate)
    return _assemble(_globals(colors), tree, s, geo, extra)


def offset_surface():
    """Superficie de un tipo que el lector no admite (PRC_TYPE_SURF_Offset)."""

    def write(w: BitWriter) -> None:
        w.uint(84)
        w.bit(0)
        w.uint(0)
    return write
