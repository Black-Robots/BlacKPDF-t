"""Lector de archivos PRC (ISO 14739-1).

Lee la estructura de ensamblaje (ocurrencias de producto, definiciones de
pieza y elementos de representación), los estilos y las teselaciones de
cada estructura de archivo, y monta una :class:`Scene`. La geometría exacta
(B-rep) no se interpreta: los visores de PDF dibujan la teselación, que es
lo que guardan los exportadores para mostrar el modelo.

Si el árbol de una estructura usa partes del formato que este lector no
entiende, se recurre a dibujar todas sus teselaciones sin colocar.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import dataclass, field

from ..progress import ProgressCallback, report
from .prc_doubles import TABLE
from .scene import IDENTITY, Material, Matrix, Part, Scene, build_lines, build_points, build_triangles, multiply

# -- tipos de entidad (PRC.h) -------------------------------------------------------

ROOT = 0
CRV = 10
SURF = 75
TOPO = 140
TESS = 170
MISC = 200
RI = 230
ASM = 300
MKP = 500
GRAPH = 700

TESS_3D = TESS + 2
TESS_3D_COMPRESSED = TESS + 3
TESS_FACE = TESS + 4
TESS_3D_WIRE = TESS + 5
TESS_MARKUP = TESS + 6

MISC_ATTRIBUTE = MISC + 1
MISC_CARTESIAN = MISC + 2
MISC_ENTITY_REFERENCE = MISC + 3
MISC_MARKUP_LINKED_ITEM = MISC + 4
MISC_REFERENCE_ON_BASE = MISC + 5
MISC_REFERENCE_ON_TOPOLOGY = MISC + 6
MISC_GENERAL = MISC + 7

RI_BREP_MODEL = RI + 2
RI_CURVE = RI + 3
RI_DIRECTION = RI + 4
RI_PLANE = RI + 5
RI_POINT_SET = RI + 6
RI_POLY_BREP_MODEL = RI + 7
RI_POLY_WIRE = RI + 8
RI_SET = RI + 9
RI_COORDINATE_SYSTEM = RI + 10

ASM_MODEL_FILE = ASM + 1
ASM_FILE_STRUCTURE = ASM + 2
ASM_GLOBALS = ASM + 3
ASM_TREE = ASM + 4
ASM_TESSELLATION = ASM + 5
ASM_PRODUCT_OCCURRENCE = ASM + 10
ASM_PART_DEFINITION = ASM + 11
ASM_FILTER = ASM + 20

MKP_VIEW = MKP + 1
MKP_MARKUP = MKP + 2
MKP_LEADER = MKP + 3
MKP_ANNOTATION_ITEM = MKP + 4
MKP_ANNOTATION_SET = MKP + 5
MKP_ANNOTATION_REFERENCE = MKP + 6

GRAPH_STYLE = GRAPH + 1
GRAPH_MATERIAL = GRAPH + 2
GRAPH_PICTURE = GRAPH + 3
GRAPH_TEXTURE_APPLICATION = GRAPH + 11
GRAPH_TEXTURE_DEFINITION = GRAPH + 12
GRAPH_TEXTURE_TRANSFORMATION = GRAPH + 13
GRAPH_LINE_PATTERN = GRAPH + 21
GRAPH_DOTTING_PATTERN = GRAPH + 23
GRAPH_HATCHING_PATTERN = GRAPH + 24
GRAPH_SOLID_PATTERN = GRAPH + 25
GRAPH_VPICTURE_PATTERN = GRAPH + 26
GRAPH_AMBIENT_LIGHT = GRAPH + 31
GRAPH_POINT_LIGHT = GRAPH + 32
GRAPH_DIRECTIONAL_LIGHT = GRAPH + 33
GRAPH_SPOT_LIGHT = GRAPH + 34
GRAPH_SCENE_DISPLAY_PARAMETERS = GRAPH + 41
GRAPH_CAMERA = GRAPH + 42

SURF_PLANE = SURF + 11

REFERENCEABLE = {
    MISC_ENTITY_REFERENCE, MISC_MARKUP_LINKED_ITEM,
    RI_BREP_MODEL, RI_CURVE, RI_DIRECTION, RI_PLANE, RI_POINT_SET, RI_POLY_BREP_MODEL, RI_POLY_WIRE, RI_SET,
    RI_COORDINATE_SYSTEM, ASM_PRODUCT_OCCURRENCE, ASM_PART_DEFINITION, ASM_FILTER,
    MKP_VIEW, MKP_MARKUP, MKP_LEADER, MKP_ANNOTATION_ITEM, MKP_ANNOTATION_SET, MKP_ANNOTATION_REFERENCE,
    GRAPH_STYLE, GRAPH_MATERIAL, GRAPH_TEXTURE_APPLICATION, GRAPH_TEXTURE_DEFINITION, GRAPH_LINE_PATTERN,
    GRAPH_DOTTING_PATTERN, GRAPH_HATCHING_PATTERN, GRAPH_SOLID_PATTERN, GRAPH_VPICTURE_PATTERN,
    GRAPH_AMBIENT_LIGHT, GRAPH_POINT_LIGHT, GRAPH_DIRECTIONAL_LIGHT, GRAPH_SPOT_LIGHT,
    GRAPH_SCENE_DISPLAY_PARAMETERS, GRAPH_CAMERA,
}

# Teselación de caras.
FACE_TRIANGLE = 0x0002
FACE_FAN = 0x0004
FACE_STRIPE = 0x0008
FACE_TRIANGLE_ONE_NORMAL = 0x0020
FACE_FAN_ONE_NORMAL = 0x0040
FACE_STRIPE_ONE_NORMAL = 0x0080
FACE_TRIANGLE_TEXTURED = 0x0200
FACE_FAN_TEXTURED = 0x0400
FACE_STRIPE_TEXTURED = 0x0800
FACE_TRIANGLE_ONE_NORMAL_TEXTURED = 0x2000
FACE_FAN_ONE_NORMAL_TEXTURED = 0x4000
FACE_STRIPE_ONE_NORMAL_TEXTURED = 0x8000
FACE_POLYFACE_TYPES = 0x0001 | 0x0010 | 0x0100 | 0x1000
NORMAL_SINGLE = 0x40000000
NORMAL_MASK = 0x3FFFFFFF
WIRE_NOT_DRAWN = 0x4000
WIRE_CLOSING = 0x8000
WIRE3D_CLOSING = 0x10000000
WIRE3D_CONTINUOUS = 0x20000000

GRAPHICS_SHOW = 0x0001
PRODUCT_SUPPRESSED = 0x01

LIMIT = 50_000_000


class PRCError(Exception):
    """El archivo no es PRC o usa una parte del formato no compatible."""


# -- tabla de dobles ------------------------------------------------------------------

_FAST_BITS = 12
_BY_LENGTH: dict[int, dict[int, tuple]] = {}
for _is_exp, _bits_count, _bits, _upper, _lower in TABLE:
    _BY_LENGTH.setdefault(_bits_count, {})[_bits] = (_is_exp, _upper, _lower, _bits_count)
_LONG_LENGTHS = sorted(length for length in _BY_LENGTH if length > _FAST_BITS)
_FAST: list = [None] * (1 << _FAST_BITS)
for _length, _codes in _BY_LENGTH.items():
    if _length > _FAST_BITS:
        continue
    for _code, _entry in _codes.items():
        _start = _code << (_FAST_BITS - _length)
        for _slot in range(_start, _start + (1 << (_FAST_BITS - _length))):
            _FAST[_slot] = _entry
_PACK = struct.Struct("<II")
_UNPACK_DOUBLE = struct.Struct("<d")


# -- lectura de bits ------------------------------------------------------------------


class BitReader:
    """Flujo de bits de una sección PRC descomprimida (bit más alto primero)."""

    __slots__ = ("data", "pos", "limit", "current_name", "current_graphics", "schema")

    def __init__(self, data: bytes, schema: dict | None = None):
        self.data = bytes(data) + b"\0" * 16
        self.pos = 0
        self.limit = len(data) * 8
        self.current_name = ""
        self.current_graphics = (-1, -1, 1)
        self.schema = schema or {}

    def _check(self) -> None:
        if self.pos > self.limit:
            raise PRCError("fin de datos")

    def bit(self) -> int:
        pos = self.pos
        self.pos = pos + 1
        if pos >= self.limit:
            raise PRCError("fin de datos")
        return (self.data[pos >> 3] >> (7 - (pos & 7))) & 1

    def bits(self, count: int) -> int:
        if not count:
            return 0
        pos = self.pos
        index = pos >> 3
        offset = pos & 7
        span = (offset + count + 7) >> 3
        chunk = int.from_bytes(self.data[index:index + span], "big")
        self.pos = pos + count
        if self.pos > self.limit:
            raise PRCError("fin de datos")
        return (chunk >> (span * 8 - offset - count)) & ((1 << count) - 1)

    def peek(self, count: int) -> int:
        pos = self.pos
        index = pos >> 3
        offset = pos & 7
        span = (offset + count + 7) >> 3
        chunk = int.from_bytes(self.data[index:index + span], "big")
        return (chunk >> (span * 8 - offset - count)) & ((1 << count) - 1)

    def boolean(self) -> bool:
        return bool(self.bit())

    def char(self) -> int:
        return self.bits(8)

    def uint(self) -> int:
        value = 0
        shift = 0
        while self.bit():
            value |= self.bits(8) << shift
            shift += 8
            if shift > 64:
                raise PRCError("entero demasiado largo")
        return value

    def int(self) -> int:
        value = 0
        shift = 0
        while self.bit():
            value |= self.bits(8) << shift
            shift += 8
            if shift > 64:
                raise PRCError("entero demasiado largo")
        if shift and value & (1 << (shift - 1)):
            value -= 1 << shift
        return value

    def string(self) -> str:
        if not self.bit():
            return ""
        length = self.uint()
        if length > LIMIT:
            raise PRCError("cadena demasiado larga")
        raw = bytes(self.bits(8) for _ in range(length))
        return raw.decode("utf-8", errors="replace")

    def double(self) -> float:
        peek = self.peek(22)
        entry = _FAST[peek >> (22 - _FAST_BITS)]
        if entry is None:
            for length in _LONG_LENGTHS:
                entry = _BY_LENGTH[length].get(peek >> (22 - length))
                if entry is not None:
                    break
            else:
                raise PRCError("double no válido")
        is_exponent, upper, lower, length = entry
        self.pos += length
        if not is_exponent and length == 2 and upper == 0 and lower == 0:
            return 0.0
        negative = self.bit()
        if is_exponent and self.bit():
            raw = bytearray(_PACK.pack(lower, upper))
            raw[6] |= self.bits(4)
            current = 5
            while current >= 0:
                if self.bit():
                    raw[current] = self.bits(8)
                else:
                    offset = self.bits(3)
                    if offset == 0:
                        previous = raw[current + 1]
                        for index in range(current, -1, -1):
                            raw[index] = previous
                        break
                    if offset == 6:
                        previous = raw[current + 1]
                        for index in range(current, 0, -1):
                            raw[index] = previous
                        raw[0] = self.bits(8)
                        break
                    raw[current] = raw[min(current + offset, 7)]
                current -= 1
        else:
            raw = bytearray(_PACK.pack(lower, upper))
        if negative:
            raw[7] |= 0x80
        return _UNPACK_DOUBLE.unpack(raw)[0]

    def vector3(self) -> tuple[float, float, float]:
        return (self.double(), self.double(), self.double())

    def vector2(self) -> tuple[float, float]:
        return (self.double(), self.double())

    def count(self) -> int:
        value = self.uint()
        if value > LIMIT:
            raise PRCError("tamaño fuera de rango")
        return value


# -- estructuras ----------------------------------------------------------------------


@dataclass
class Graphics:
    layer: int = -1
    style: int = -1
    behaviour: int = 1


@dataclass
class Style:
    width: float = 0.0
    is_material: bool = False
    index: int = -1  # color o material
    transparency: int | None = None


@dataclass
class MaterialDef:
    kind: str  # "generic" o "texture"
    ambient: int = -1
    diffuse: int = -1
    emissive: int = -1
    specular: int = -1
    shininess: float = 0.3
    diffuse_alpha: float = 1.0
    generic_index: int = -1


@dataclass
class RepresentationItem:
    kind: int
    name: str
    graphics: Graphics
    coordinate_system: int
    tessellation: int
    children: list = field(default_factory=list)
    points: list = field(default_factory=list)
    transform: Matrix | None = None


@dataclass
class PartDefinition:
    name: str
    graphics: Graphics
    items: list


@dataclass
class ProductOccurrence:
    name: str
    graphics: Graphics
    part: int
    prototype: int
    prototype_fs: tuple | None
    external: int
    external_fs: tuple | None
    sons: list
    behaviour: int
    location: Matrix | None


@dataclass
class Tessellation:
    kind: int
    coordinates: list
    normals: list = field(default_factory=list)
    triangulated: list = field(default_factory=list)
    wire_indices: list = field(default_factory=list)
    faces: list = field(default_factory=list)
    recalculate_normals: bool = False
    crease_angle: float = 40.0
    vertex_colors: list | None = None
    codes: list = field(default_factory=list)


@dataclass
class TessFace:
    line_attributes: list
    start_wire: int
    sizes_wire: list
    used_entities: int
    start_triangulated: int
    sizes_triangulated: list
    texture_indexes: int
    colors: list | None
    behaviour: int


@dataclass
class FileStructure:
    uuid: tuple
    colors: list = field(default_factory=list)
    materials: list = field(default_factory=list)
    styles: list = field(default_factory=list)
    coordinate_systems: list = field(default_factory=list)
    parts: list = field(default_factory=list)
    occurrences: list = field(default_factory=list)
    root: int = -1
    tessellations: list = field(default_factory=list)
    tree_complete: bool = False
    schema: dict = field(default_factory=dict)


# -- contenido común ------------------------------------------------------------------


def _attribute_entry(r: BitReader) -> None:
    if r.boolean():
        r.uint()
    else:
        r.string()


def _attributes(r: BitReader) -> None:
    for _ in range(r.count()):
        if r.uint() != MISC_ATTRIBUTE:
            raise PRCError("atributo no válido")
        _attribute_entry(r)
        for _ in range(r.count()):
            _attribute_entry(r)
            kind = r.uint()
            if kind == 1:
                r.int()
            elif kind == 2:
                r.double()
            elif kind == 3:
                r.uint()
            elif kind == 4:
                r.string()


def _name(r: BitReader) -> str:
    if r.boolean():
        return r.current_name
    name = r.string()
    r.current_name = name
    return name


def _content_base(r: BitReader, kind: int) -> str:
    _attributes(r)
    name = _name(r)
    if kind in REFERENCEABLE:
        r.uint()
        r.uint()
        r.uint()
    _extensions(r, PRCBASE)
    return name


def _graphics(r: BitReader) -> Graphics:
    if r.boolean():
        layer, style, behaviour = r.current_graphics
    else:
        layer = r.uint() - 1
        style = r.uint() - 1
        low = r.char()
        behaviour = low | (r.char() << 8)
        r.current_graphics = (layer, style, behaviour)
    return Graphics(layer, style, behaviour)


def _base_with_graphics(r: BitReader, kind: int) -> tuple[str, Graphics]:
    name = _content_base(r, kind)
    graphics = _graphics(r)
    _extensions(r, PRCBASE_WITH_GRAPHICS)
    return name, graphics


def _user_data(r: BitReader) -> None:
    size = r.uint()
    if size > LIMIT * 8:
        raise PRCError("datos de usuario demasiado grandes")
    r.pos += size
    r._check()


def _compressed_uid(r: BitReader) -> tuple:
    return (r.uint(), r.uint(), r.uint(), r.uint())


def _expect(r: BitReader, kind: int) -> None:
    found = r.uint()
    if found != kind:
        raise PRCError(f"se esperaba el tipo {kind} y hay {found}")


# -- transformaciones -----------------------------------------------------------------


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _cartesian_matrix(behaviour, origin, x_axis, y_axis, z_axis, scale, nonuniform) -> Matrix:
    x = list(x_axis)
    y = list(y_axis)
    if z_axis is not None:
        z = list(z_axis)
    elif behaviour & 0x04:  # simetría
        z = list(_cross(y_axis, x_axis))
    else:
        z = list(_cross(x_axis, y_axis))
    if nonuniform is not None:
        x = [v * nonuniform[0] for v in x]
        y = [v * nonuniform[1] for v in y]
        z = [v * nonuniform[2] for v in z]
    elif scale is not None:
        x = [v * scale for v in x]
        y = [v * scale for v in y]
        z = [v * scale for v in z]
    return (x[0], x[1], x[2], 0.0, y[0], y[1], y[2], 0.0, z[0], z[1], z[2], 0.0,
            origin[0], origin[1], origin[2], 1.0)


def _cartesian_content(r: BitReader) -> Matrix:
    behaviour = r.char()
    origin = r.vector3() if behaviour & 0x01 else (0.0, 0.0, 0.0)
    x_axis, y_axis, z_axis = (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), None
    if behaviour & 0x20:
        x_axis, y_axis, z_axis = r.vector3(), r.vector3(), r.vector3()
    elif behaviour & 0x02:
        x_axis, y_axis = r.vector3(), r.vector3()
    scale = nonuniform = None
    if behaviour & 0x10:
        nonuniform = r.vector3()
    elif behaviour & 0x08:
        scale = r.double()
    if behaviour & 0x40:
        r.double()
        r.double()
        r.double()
        r.double()
    return _cartesian_matrix(behaviour, origin, x_axis, y_axis, z_axis, scale, nonuniform)


def _transformation3d(r: BitReader) -> Matrix:
    kind = r.uint()
    if kind == MISC_CARTESIAN:
        return _cartesian_content(r)
    if kind == MISC_GENERAL:
        return tuple(r.double() for _ in range(16))
    raise PRCError(f"transformación desconocida {kind}")


def _surface_transformation(r: BitReader) -> None:
    """Transformación de superficies (bit de presencia y comportamiento)."""
    if not r.bit():
        return
    behaviour = r.char()
    if behaviour & 0x01:
        r.vector3()
    if behaviour & 0x02:
        r.vector3()
        r.vector3()
    if behaviour & 0x08:
        r.double()


def _base_geometry(r: BitReader) -> None:
    if r.boolean():
        _attributes(r)
        _name(r)
        r.uint()


def _plane_surface(r: BitReader) -> None:
    _expect(r, SURF_PLANE)
    _base_geometry(r)
    r.uint()  # extensión
    _surface_transformation(r)
    r.boolean()  # intercambio uv
    r.vector2()
    r.vector2()
    for _ in range(4):
        r.double()


# -- globales -------------------------------------------------------------------------


def _schema(r: BitReader) -> dict:
    """Definiciones de schema: tipo de entidad -> lista de tokens."""
    result = {}
    for _ in range(r.count()):
        kind = r.uint()
        result[kind] = [r.uint() for _ in range(r.count())]
    return result


READER_VERSION = 8137
"""Versión de la norma que este lector entiende sin ayuda del schema."""

PRCBASE = ROOT + 1
PRCBASE_WITH_GRAPHICS = ROOT + 2


class _SchemaRunner:
    """Ejecuta los tokens de schema para leer (y descartar) datos nuevos."""

    def __init__(self, r: BitReader, depth: int = 0):
        self.r = r
        self.depth = depth
        self.variables: dict[int, float] = {}
        self.loop = 0

    def run(self, tokens: list[int]) -> None:
        position = 0
        while position < len(tokens):
            position = self.instruction(tokens, position)

    def _skip_block(self, tokens: list[int], position: int) -> int:
        """Salta hasta el Block_End que cierra el bloque abierto antes de ``position``."""
        level = 1
        while position < len(tokens):
            token = tokens[position]
            if token in (19, 20):
                level += 1
                position += 2 if token == 20 else 1
                continue
            if token == 21:
                level -= 1
                position += 1
                if level == 0:
                    return position
                continue
            position += 2 if token in _TOKENS_WITH_ARGUMENT else 1
        return position

    def _block(self, tokens: list[int], position: int) -> int:
        while position < len(tokens) and tokens[position] != 21:
            position = self.instruction(tokens, position)
        return position + 1

    def instruction(self, tokens: list[int], position: int) -> int:
        if self.depth > 32:
            raise PRCError("schema demasiado anidado")
        token = tokens[position]
        position += 1
        if token == 19:  # Block_Start
            return self._block(tokens, position)
        if token == 20:  # Block_Version
            version = tokens[position]
            position += 1
            if version <= READER_VERSION:
                return self._skip_block(tokens, position)
            return self._block(tokens, position)
        if token == 21:
            return position
        if token == 39:  # marca sin datos (versiones recientes)
            return position + 1
        if token == 6:  # Father_Type: datos del tipo indicado
            kind = tokens[position]
            nested = self.r.schema.get(kind)
            if nested is not None:
                _SchemaRunner(self.r, self.depth + 1).run(nested)
            return position + 1
        if token == 15:  # For
            count, position = self.value(tokens, position)
            start = position
            end = position
            for index in range(int(count)):
                self.loop = index
                end = self.instruction(tokens, start)
            return end if count else self._skip_instruction(tokens, start)
        if token == 16:  # SimpleFor
            count = self.r.uint()
            start = position
            end = position
            for index in range(min(count, LIMIT)):
                self.loop = index
                end = self.instruction(tokens, start)
            return end if count else self._skip_instruction(tokens, start)
        if token == 17:  # If
            condition, position = self.value(tokens, position)
            if condition:
                position = self.instruction(tokens, position)
                if position < len(tokens) and tokens[position] == 18:
                    position = self._skip_instruction(tokens, position + 1)
            else:
                position = self._skip_instruction(tokens, position)
                if position < len(tokens) and tokens[position] == 18:
                    position = self.instruction(tokens, position + 1)
            return position
        if token in (22, 23, 24):
            index = tokens[position]
            position += 1
            if token == 22:
                self.variables[index] = 0
                return position
            value, position = self.value(tokens, position)
            self.variables[index] = value
            return position
        _, position = self.value(tokens, position - 1)
        return position

    def _skip_instruction(self, tokens: list[int], position: int) -> int:
        """Avanza sobre una instrucción sin leer datos."""
        token = tokens[position]
        position += 1
        if token in (19, 20):
            if token == 20:
                position += 1
            return self._skip_block(tokens, position)
        if token in _TOKENS_WITH_ARGUMENT:
            return position + 1
        if token in _BINARY_OPERATORS:
            position = self._skip_instruction(tokens, position)
            return self._skip_instruction(tokens, position)
        if token == 15:
            position = self._skip_instruction(tokens, position)
            return self._skip_instruction(tokens, position)
        if token == 16:
            return self._skip_instruction(tokens, position)
        if token == 17:
            position = self._skip_instruction(tokens, position)
            position = self._skip_instruction(tokens, position)
            if position < len(tokens) and tokens[position] == 18:
                position = self._skip_instruction(tokens, position + 1)
            return position
        if token in (23, 24):
            return self._skip_instruction(tokens, position + 1)
        return position

    def value(self, tokens: list[int], position: int) -> tuple[float, int]:
        token = tokens[position]
        position += 1
        r = self.r
        if token == 0:
            return float(r.boolean()), position
        if token == 1:
            return r.double(), position
        if token == 2:
            return float(r.char()), position
        if token == 3:
            return float(r.uint()), position
        if token == 4:
            return float(r.int()), position
        if token == 5:
            r.string()
            return 0.0, position
        if token == 7:
            r.vector2()
            return 0.0, position
        if token == 8:
            r.vector3()
            return 0.0, position
        if token == 9:
            r.double()
            r.double()
            return 0.0, position
        if token == 10:
            for _ in range(4):
                r.double()
            return 0.0, position
        if token == 11:
            for _ in range(6):
                r.double()
            return 0.0, position
        if token in (12, 13, 14):
            raise PRCError("schema con objetos anidados no compatible")
        if token == 25:
            return self.variables.get(tokens[position], 0.0), position + 1
        if token == 26:
            return float(tokens[position]), position + 1
        if token == 27:
            return float(self.loop), position
        if token == 28:
            return 1.0, position
        if token in _BINARY_OPERATORS:
            left, position = self.value(tokens, position)
            right, position = self.value(tokens, position)
            return _BINARY_OPERATORS[token](left, right), position
        raise PRCError(f"token de schema desconocido {token}")


_TOKENS_WITH_ARGUMENT = {6, 12, 22, 25, 26, 39}
_BINARY_OPERATORS = {
    29: lambda a, b: a * b,
    30: lambda a, b: a / b if b else 0.0,
    31: lambda a, b: a + b,
    32: lambda a, b: a - b,
    33: lambda a, b: float(a < b),
    34: lambda a, b: float(a <= b),
    35: lambda a, b: float(a > b),
    36: lambda a, b: float(a >= b),
    37: lambda a, b: float(a == b),
    38: lambda a, b: float(a != b),
}


def _extensions(r: BitReader, *kinds: int) -> None:
    """Lee los datos que el schema añade a los tipos indicados (base primero)."""
    if not r.schema:
        return
    for kind in kinds:
        tokens = r.schema.get(kind)
        if tokens:
            _SchemaRunner(r).run(tokens)


def _end_graphics_entity(r: BitReader, kind: int) -> None:
    """Final de una entidad: ampliaciones del schema de su tipo y datos de usuario."""
    _extensions(r, kind)
    _user_data(r)


def _end_base_entity(r: BitReader, kind: int, user_data: bool = True) -> None:
    _extensions(r, kind)
    if user_data:
        _user_data(r)


def _material(r: BitReader) -> MaterialDef:
    kind = r.uint()
    if kind == GRAPH_MATERIAL:
        _content_base(r, kind)
        material = MaterialDef("generic")
        material.ambient = r.uint() - 1
        material.diffuse = r.uint() - 1
        material.emissive = r.uint() - 1
        material.specular = r.uint() - 1
        material.shininess = r.double()
        r.double()
        material.diffuse_alpha = r.double()
        r.double()
        r.double()
        _extensions(r, kind)
        return material
    if kind == GRAPH_TEXTURE_APPLICATION:
        _content_base(r, kind)
        material = MaterialDef("texture")
        material.generic_index = r.uint() - 1
        r.uint()
        r.uint()
        r.uint()
        _extensions(r, kind)
        return material
    raise PRCError(f"material desconocido {kind}")


def _texture_definition(r: BitReader) -> None:
    _expect(r, GRAPH_TEXTURE_DEFINITION)
    _content_base(r, GRAPH_TEXTURE_DEFINITION)
    r.uint()
    dimension = r.char()
    mapping = r.int()
    if mapping == 3:  # operador de proyección
        r.int()
        if r.bit():
            _transformation3d(r)
    r.uint()
    for _ in range(r.count()):
        r.double()
    for _ in range(r.count()):
        r.char()
    function = r.int()
    if function == 3:  # mezcla
        for _ in range(4):
            r.double()
    if r.int() != 0:
        r.int()
    if r.int() != 0:
        r.int()
    mode = r.char()
    if mode & 0x02:
        r.int()
        r.double()
    r.int()
    if dimension > 1:
        r.int()
    if dimension > 2:
        r.int()
    if r.bit():
        _expect(r, GRAPH_TEXTURE_TRANSFORMATION)
        r.boolean()
        r.boolean()
        is_2d = r.boolean()
        if r.bit():
            behaviour = r.char()
            vector = r.vector2 if is_2d else r.vector3
            if behaviour & 0x01:
                vector()
            if behaviour & 0x20:
                vector()
                vector()
                if not is_2d:
                    r.vector3()
            elif behaviour & 0x02:
                vector()
                vector()
            if behaviour & 0x10:
                vector()
            elif behaviour & 0x08:
                r.double()
            if behaviour & 0x40:
                for _ in range(3 if is_2d else 4):
                    r.double()
    _extensions(r, GRAPH_TEXTURE_DEFINITION)


def _line_pattern(r: BitReader) -> None:
    _expect(r, GRAPH_LINE_PATTERN)
    _content_base(r, GRAPH_LINE_PATTERN)
    for _ in range(r.count()):
        r.double()
    r.double()
    r.boolean()
    _extensions(r, GRAPH_LINE_PATTERN)


def _style(r: BitReader) -> Style:
    _expect(r, GRAPH_STYLE)
    _content_base(r, GRAPH_STYLE)
    style = Style()
    style.width = r.double()
    r.boolean()
    r.uint()
    style.is_material = r.boolean()
    style.index = r.uint() - 1
    if r.boolean():
        style.transparency = r.char()
    for _ in range(3):
        if r.boolean():
            r.char()
    _extensions(r, GRAPH_STYLE)
    return style


def _markup_tess_codes(r: BitReader) -> None:
    for _ in range(r.count()):
        r.uint()
    for _ in range(r.count()):
        r.string()
    r.string()
    r.char()


def _fill_pattern(r: BitReader) -> None:
    kind = r.uint()
    _content_base(r, kind)
    if kind == GRAPH_DOTTING_PATTERN:
        r.uint()
        r.double()
        r.boolean()
        r.int()
    elif kind == GRAPH_HATCHING_PATTERN:
        r.uint()
        for _ in range(r.count()):
            for _ in range(5):
                r.double()
            r.int()
    elif kind == GRAPH_SOLID_PATTERN:
        r.uint()
        r.boolean()
        r.uint()
    elif kind == GRAPH_VPICTURE_PATTERN:
        r.uint()
        r.double()
        r.double()
        _expect(r, TESS_MARKUP)
        _base_tess_coordinates(r)
        _markup_tess_codes(r)
    else:
        raise PRCError(f"patrón desconocido {kind}")
    _extensions(r, kind)


def _representation_content(r: BitReader, kind: int) -> tuple[str, Graphics, int, int]:
    name, graphics = _base_with_graphics(r, kind)
    coordinate_system = r.uint() - 1
    tessellation = r.uint() - 1
    return name, graphics, coordinate_system, tessellation


def _coordinate_system(r: BitReader) -> Matrix:
    _expect(r, RI_COORDINATE_SYSTEM)
    _representation_content(r, RI_COORDINATE_SYSTEM)
    matrix = _transformation3d(r)
    _end_graphics_entity(r, RI_COORDINATE_SYSTEM)
    return matrix


def _globals(r: BitReader, fs: FileStructure) -> None:
    fs.schema = r.schema = _schema(r)
    _expect(r, ASM_GLOBALS)
    _content_base(r, ASM_GLOBALS)
    for _ in range(r.count()):
        _compressed_uid(r)
    r.double()
    r.double()
    r.string()
    for _ in range(r.count()):
        r.string()
        r.uint()
        for _ in range(r.count()):
            r.uint()
            r.char()
    for _ in range(r.count()):
        fs.colors.append((r.double(), r.double(), r.double()))
    for _ in range(r.count()):
        _expect(r, GRAPH_PICTURE)
        _content_base(r, GRAPH_PICTURE)
        r.int()
        r.uint()
        r.uint()
        r.uint()
        _extensions(r, GRAPH_PICTURE)
    for _ in range(r.count()):
        _texture_definition(r)
    for _ in range(r.count()):
        fs.materials.append(_material(r))
    for _ in range(r.count()):
        _line_pattern(r)
    for _ in range(r.count()):
        fs.styles.append(_style(r))
    for _ in range(r.count()):
        _fill_pattern(r)
    for _ in range(r.count()):
        fs.coordinate_systems.append(_coordinate_system(r))
    _end_base_entity(r, ASM_GLOBALS)


# -- árbol -------------------------------------------------------------------------------


def _reference_unique_identifier(r: BitReader) -> None:
    _expect(r, MISC_REFERENCE_ON_BASE)
    r.uint()
    if not r.boolean():
        _compressed_uid(r)
    r.uint()


def _reference_data(r: BitReader) -> None:
    kind = r.uint()
    if kind == MISC_REFERENCE_ON_TOPOLOGY:
        r.uint()
        if r.boolean():
            if not r.boolean():
                _compressed_uid(r)
            r.uint()
            r.uint()
            for _ in range(r.count()):
                r.uint()
    elif kind == MISC_REFERENCE_ON_BASE:
        r.uint()
        if not r.boolean():
            _compressed_uid(r)
        r.uint()
    else:
        raise PRCError(f"referencia desconocida {kind}")


def _entity_reference_content(r: BitReader, kind: int) -> None:
    _base_with_graphics(r, kind)
    r.uint()  # sistema de coordenadas local + 1
    if r.boolean():
        _reference_data(r)


def _entity_reference(r: BitReader) -> None:
    _expect(r, MISC_ENTITY_REFERENCE)
    _entity_reference_content(r, MISC_ENTITY_REFERENCE)
    _end_graphics_entity(r, MISC_ENTITY_REFERENCE)


def _linked_item(r: BitReader) -> None:
    _expect(r, MISC_MARKUP_LINKED_ITEM)
    _entity_reference_content(r, MISC_MARKUP_LINKED_ITEM)
    for _ in range(4):
        r.boolean()
    _end_graphics_entity(r, MISC_MARKUP_LINKED_ITEM)


def _references(r: BitReader) -> None:
    for _ in range(r.count()):
        _reference_unique_identifier(r)


def _annotation_entity(r: BitReader, depth: int = 0) -> None:
    if depth > 64:
        raise PRCError("anotaciones demasiado anidadas")
    kind = r.uint()
    _base_with_graphics(r, kind)
    if kind == MKP_ANNOTATION_ITEM:
        _reference_unique_identifier(r)
        _end_graphics_entity(r, kind)
    elif kind == MKP_ANNOTATION_SET:
        for _ in range(r.count()):
            _annotation_entity(r, depth + 1)
        _end_graphics_entity(r, kind)
    elif kind == MKP_ANNOTATION_REFERENCE:
        _references(r)
        _end_graphics_entity(r, kind)
    else:
        raise PRCError(f"anotación desconocida {kind}")


def _markups(r: BitReader) -> None:
    for _ in range(r.count()):
        _linked_item(r)
    for _ in range(r.count()):
        _expect(r, MKP_LEADER)
        _base_with_graphics(r, MKP_LEADER)
        _references(r)
        _references(r)
        r.uint()
        _end_graphics_entity(r, MKP_LEADER)
    for _ in range(r.count()):
        _expect(r, MKP_MARKUP)
        _base_with_graphics(r, MKP_MARKUP)
        r.uint()
        r.uint()
        _references(r)
        _references(r)
        r.uint()
        _end_graphics_entity(r, MKP_MARKUP)
    for _ in range(r.count()):
        _annotation_entity(r)


def _camera(r: BitReader) -> None:
    _expect(r, GRAPH_CAMERA)
    _content_base(r, GRAPH_CAMERA)
    r.boolean()
    r.vector3()
    r.vector3()
    r.vector3()
    for _ in range(6):
        r.double()
    _extensions(r, GRAPH_CAMERA)


def _light(r: BitReader) -> None:
    kind = r.uint()
    _content_base(r, kind)
    for _ in range(4):
        r.uint()
    if kind == GRAPH_POINT_LIGHT:
        r.vector3()
        r.double()
        r.double()
        r.double()
    elif kind == GRAPH_DIRECTIONAL_LIGHT:
        r.vector3()
        r.double()
    elif kind == GRAPH_SPOT_LIGHT:
        r.vector3()
        r.double()
        r.double()
        r.double()
        r.vector3()
        r.double()
        r.double()
    elif kind != GRAPH_AMBIENT_LIGHT:
        raise PRCError(f"luz desconocida {kind}")
    _extensions(r, kind)


def _scene_display_parameters(r: BitReader) -> None:
    _expect(r, GRAPH_SCENE_DISPLAY_PARAMETERS)
    _content_base(r, GRAPH_SCENE_DISPLAY_PARAMETERS)
    r.boolean()
    for _ in range(r.count()):
        _light(r)
    if r.boolean():
        _camera(r)
    if r.boolean():
        r.vector3()
    for _ in range(r.count()):
        _plane_surface(r)
    r.uint()
    r.uint()
    for _ in range(r.count()):
        r.uint()
        r.uint()
    r.boolean()
    _extensions(r, GRAPH_SCENE_DISPLAY_PARAMETERS)


def _filter(r: BitReader) -> None:
    _expect(r, ASM_FILTER)
    _content_base(r, ASM_FILTER)
    r.boolean()
    r.boolean()
    for _ in range(r.count()):
        r.uint()
    r.boolean()
    for _ in range(r.count()):
        _entity_reference(r)
    _end_base_entity(r, ASM_FILTER)


def _view(r: BitReader) -> None:
    _expect(r, MKP_VIEW)
    _base_with_graphics(r, MKP_VIEW)
    _references(r)
    _plane_surface(r)
    if r.bit():
        _scene_display_parameters(r)
    r.boolean()
    r.boolean()
    r.boolean()
    _references(r)
    for _ in range(r.count()):
        _filter(r)
    _end_graphics_entity(r, MKP_VIEW)


def _representation_item(r: BitReader, depth: int = 0) -> RepresentationItem:
    if depth > 64:
        raise PRCError("conjuntos demasiado anidados")
    kind = r.uint()
    name, graphics, coordinate_system, tessellation = _representation_content(r, kind)
    item = RepresentationItem(kind, name, graphics, coordinate_system, tessellation)
    if kind == RI_BREP_MODEL:
        if r.bit():
            r.uint()
            r.uint()
        r.boolean()
    elif kind in (RI_CURVE, RI_PLANE):
        if r.bit():
            r.uint()
            r.uint()
    elif kind == RI_DIRECTION:
        if r.bit():
            r.vector3()
        r.vector3()
    elif kind == RI_POINT_SET:
        item.points = [r.vector3() for _ in range(r.count())]
    elif kind == RI_POLY_BREP_MODEL:
        r.boolean()
    elif kind == RI_POLY_WIRE:
        pass
    elif kind == RI_SET:
        item.children = [_representation_item(r, depth + 1) for _ in range(r.count())]
    elif kind == RI_COORDINATE_SYSTEM:
        item.transform = _transformation3d(r)
    else:
        raise PRCError(f"elemento desconocido {kind}")
    _end_graphics_entity(r, kind)
    return item


def _part_definition(r: BitReader) -> PartDefinition:
    _expect(r, ASM_PART_DEFINITION)
    name, graphics = _base_with_graphics(r, ASM_PART_DEFINITION)
    r.vector3()
    r.vector3()
    items = [_representation_item(r) for _ in range(r.count())]
    _markups(r)
    for _ in range(r.count()):
        _view(r)
    _end_graphics_entity(r, ASM_PART_DEFINITION)
    return PartDefinition(name, graphics, items)


def _product_occurrence(r: BitReader) -> ProductOccurrence:
    _expect(r, ASM_PRODUCT_OCCURRENCE)
    name, graphics = _base_with_graphics(r, ASM_PRODUCT_OCCURRENCE)
    part = r.uint() - 1
    prototype = r.uint() - 1
    prototype_fs = None
    if prototype != -1 and not r.boolean():
        prototype_fs = _compressed_uid(r)
    external = r.uint() - 1
    external_fs = None
    if external != -1 and not r.boolean():
        external_fs = _compressed_uid(r)
    sons = [r.uint() for _ in range(r.count())]
    behaviour = r.char()
    r.boolean()  # unidad del CAD
    r.double()  # unidad
    r.char()  # indicadores
    r.int()  # estado de carga
    location = _transformation3d(r) if r.bit() else None
    for _ in range(r.count()):
        _entity_reference(r)
    _markups(r)
    for _ in range(r.count()):
        _view(r)
    if r.bit():
        _filter(r)
    for _ in range(r.count()):
        _filter(r)
    for _ in range(r.count()):
        _scene_display_parameters(r)
    _end_graphics_entity(r, ASM_PRODUCT_OCCURRENCE)
    return ProductOccurrence(name, graphics, part, prototype, prototype_fs, external, external_fs, sons, behaviour, location)


def _tree(r: BitReader, fs: FileStructure) -> None:
    _expect(r, ASM_TREE)
    _content_base(r, ASM_TREE)
    for _ in range(r.count()):
        fs.parts.append(_part_definition(r))
    for _ in range(r.count()):
        fs.occurrences.append(_product_occurrence(r))
    _expect(r, ASM_FILE_STRUCTURE)
    _content_base(r, ASM_FILE_STRUCTURE)
    r.uint()
    fs.root = r.uint() - 1
    _extensions(r, ASM_FILE_STRUCTURE)
    _end_base_entity(r, ASM_TREE)
    fs.tree_complete = True


# -- teselación ---------------------------------------------------------------------


def _base_tess_coordinates(r: BitReader) -> list:
    r.boolean()
    count = r.count()
    double = r.double
    return [double() for _ in range(count)]




def _read_colors(r: BitReader, count: int, is_rgba: bool) -> list:
    size = 4 if is_rgba else 3
    if count <= 0:
        return []
    first = tuple(r.char() for _ in range(size))
    colors = [first]
    for _ in range(count - 1):
        if r.boolean():
            colors.append(colors[-1])
        else:
            colors.append(tuple(r.char() for _ in range(size)))
    return colors


def _face_point_count(face_used: int, sizes: list) -> int:
    """Número de vértices que describen los triángulos de una cara."""
    total = 0
    k = 0
    for bit in (FACE_TRIANGLE, FACE_FAN, FACE_STRIPE, FACE_TRIANGLE_ONE_NORMAL, FACE_FAN_ONE_NORMAL,
                FACE_STRIPE_ONE_NORMAL, FACE_TRIANGLE_TEXTURED, FACE_FAN_TEXTURED, FACE_STRIPE_TEXTURED,
                FACE_TRIANGLE_ONE_NORMAL_TEXTURED, FACE_FAN_ONE_NORMAL_TEXTURED, FACE_STRIPE_ONE_NORMAL_TEXTURED):
        if not face_used & bit:
            continue
        if bit in (FACE_TRIANGLE, FACE_TRIANGLE_ONE_NORMAL, FACE_TRIANGLE_TEXTURED, FACE_TRIANGLE_ONE_NORMAL_TEXTURED):
            total += sizes[k] * 3
            k += 1
        else:
            entities = sizes[k]
            k += 1
            for _ in range(entities):
                total += sizes[k] & NORMAL_MASK
                k += 1
    return total


def _tess_face(r: BitReader) -> TessFace:
    _expect(r, TESS_FACE)
    line_attributes = [r.uint() - 1 for _ in range(r.count())]
    start_wire = r.uint()
    sizes_wire = [r.uint() for _ in range(r.count())]
    used = r.uint()
    start_triangulated = r.uint()
    sizes_triangulated = [r.uint() for _ in range(r.count())]
    texture_indexes = r.uint()
    colors = None
    if r.boolean():
        is_rgba = r.boolean()
        if r.boolean():
            raise PRCError("colores optimizados no compatibles")
        colors = _read_colors(r, _face_point_count(used, sizes_triangulated), is_rgba)
    behaviour = r.uint() if line_attributes else 1
    return TessFace(line_attributes, start_wire, sizes_wire, used, start_triangulated, sizes_triangulated,
                    texture_indexes, colors, behaviour)


def _tess_3d(r: BitReader) -> Tessellation:
    tess = Tessellation(TESS_3D, _base_tess_coordinates(r))
    r.boolean()  # has_faces
    r.boolean()  # has_loops
    tess.recalculate_normals = r.boolean()
    if tess.recalculate_normals:
        r.char()
        angle = r.double()
        if math.isfinite(angle) and 0 < angle < 180:
            tess.crease_angle = angle
    double = r.double
    tess.normals = [double() for _ in range(r.count())]
    uint = r.uint
    tess.wire_indices = [uint() for _ in range(r.count())]
    tess.triangulated = [uint() for _ in range(r.count())]
    tess.faces = [_tess_face(r) for _ in range(r.count())]
    for _ in range(r.count()):
        double()
    return tess


def _tess_wire(r: BitReader) -> Tessellation:
    tess = Tessellation(TESS_3D_WIRE, _base_tess_coordinates(r))
    tess.wire_indices = [r.uint() for _ in range(r.count())]
    if r.boolean():
        is_rgba = r.boolean()
        r.boolean()
        if r.boolean():
            raise PRCError("colores optimizados no compatibles")
        count = 0
        index = 0
        indices = tess.wire_indices
        if not indices:
            count = len(tess.coordinates) // 3
        while index < len(indices):
            raw = indices[index]
            points = raw & 0x0FFFFFFF
            count += points + (1 if raw & WIRE3D_CLOSING else 0)
            index += 1 + points
        tess.vertex_colors = _read_colors(r, count, is_rgba)
    return tess


def _tess_markup(r: BitReader) -> Tessellation:
    tess = Tessellation(TESS_MARKUP, _base_tess_coordinates(r))
    tess.codes = [r.uint() for _ in range(r.count())]
    for _ in range(r.count()):
        r.string()
    r.string()
    r.char()
    return tess


def _tessellations(r: BitReader, fs: FileStructure, progress) -> None:
    _expect(r, ASM_TESSELLATION)
    _content_base(r, ASM_TESSELLATION)
    count = r.count()
    for index in range(count):
        kind = r.uint()
        if kind == TESS_3D:
            fs.tessellations.append(_tess_3d(r))
        elif kind == TESS_3D_WIRE:
            fs.tessellations.append(_tess_wire(r))
        elif kind == TESS_MARKUP:
            fs.tessellations.append(_tess_markup(r))
        elif kind == TESS_3D_COMPRESSED:
            from .prc_compressed import read_compressed_tessellation

            fs.tessellations.append(read_compressed_tessellation(r))
        else:
            raise PRCError(f"teselación desconocida {kind}")
        if progress is not None and index % 64 == 0:
            report(progress, 0, 1)


# -- archivo ---------------------------------------------------------------------------


def _inflate(data: bytes, offset: int) -> bytes:
    if offset <= 0 or offset >= len(data):
        raise PRCError("sección fuera del archivo")
    try:
        decoder = zlib.decompressobj()
        return decoder.decompress(data[offset:], LIMIT * 4)
    except zlib.error as error:
        raise PRCError(f"sección dañada ({error})") from error


class PRCFile:
    def __init__(self, data: bytes, progress: ProgressCallback | None = None):
        if data[:3] != b"PRC":
            raise PRCError("no es un archivo PRC")
        self.data = data
        self.progress = progress
        self.structures: list[FileStructure] = []
        self.roots: list[tuple[tuple, int]] = []
        self.warnings: list[str] = []
        self._parse()

    def _parse(self) -> None:
        data = self.data
        try:
            count = struct.unpack_from("<I", data, 43)[0]
            if count > 10_000:
                raise PRCError("demasiadas estructuras")
            offset = 47
            infos = []
            for _ in range(count):
                uuid = struct.unpack_from("<4I", data, offset)
                offset += 16
                offset += 4  # reservado
                sections = struct.unpack_from("<I", data, offset)[0]
                offset += 4
                offsets = struct.unpack_from(f"<{sections}I", data, offset)
                offset += 4 * sections
                infos.append((uuid, offsets))
            model_offset = struct.unpack_from("<I", data, offset)[0]
        except struct.error as error:
            raise PRCError("cabecera dañada") from error

        for uuid, offsets in infos:
            fs = FileStructure(uuid)
            self.structures.append(fs)
            if len(offsets) < 4:
                self.warnings.append("prc-sections")
                continue
            try:
                _globals(BitReader(_inflate(data, offsets[1])), fs)
            except (PRCError, IndexError, ValueError, OverflowError) as error:
                self.warnings.append(f"prc-globals: {error}")
            try:
                _tessellations(BitReader(_inflate(data, offsets[3]), fs.schema), fs, self.progress)
            except (PRCError, IndexError, ValueError, OverflowError, ImportError) as error:
                self.warnings.append(f"prc-tessellation: {error}")
            try:
                _tree(BitReader(_inflate(data, offsets[2]), fs.schema), fs)
            except (PRCError, IndexError, ValueError, OverflowError) as error:
                self.warnings.append(f"prc-tree: {error}")
            if self.progress is not None:
                report(self.progress, 0, 1)
        try:
            r = BitReader(_inflate(data, model_offset))
            r.schema = _schema(r)
            _expect(r, ASM_MODEL_FILE)
            _content_base(r, ASM_MODEL_FILE)
            r.boolean()
            r.double()
            for _ in range(r.count()):
                uid = _compressed_uid(r)
                index = r.uint()
                r.boolean()
                self.roots.append((uid, index - 1))
        except (PRCError, IndexError, ValueError, OverflowError):
            self.roots = []

    # -- montaje -------------------------------------------------------------------

    def _structure(self, uid: tuple | None, default: FileStructure) -> FileStructure | None:
        if uid is None:
            return default
        for fs in self.structures:
            if fs.uuid == uid:
                return fs
        return None

    def scene(self) -> Scene:
        scene = Scene(format="PRC", warnings=list(self.warnings))
        builder = _SceneBuilder(scene)
        visited_any = False
        roots = []
        for uid, index in self.roots:
            fs = self._structure(uid, None)
            if fs is not None:
                roots.append((fs, index))
        if not roots:
            for fs in self.structures:
                if fs.occurrences:
                    roots.append((fs, fs.root if 0 <= fs.root < len(fs.occurrences) else len(fs.occurrences) - 1))
        used_tessellations: set[tuple[int, int]] = set()
        for fs, index in roots:
            if 0 <= index < len(fs.occurrences):
                self._visit(builder, fs, index, IDENTITY, None, True, used_tessellations, 0)
                visited_any = True
        # Teselaciones que el árbol no coloca (árbol incompleto o sin estructura).
        for fs_index, fs in enumerate(self.structures):
            if fs.tree_complete and visited_any:
                continue
            for tess_index, tess in enumerate(fs.tessellations):
                if (id(fs), tess_index) in used_tessellations or tess is None:
                    continue
                if tess.kind == TESS_MARKUP:
                    continue
                builder.add_tessellation(fs, tess, f"Pieza {tess_index + 1}", IDENTITY, None, True)
        return scene

    def _visit(self, builder, fs, index, matrix, style, visible, used, depth) -> None:
        if depth > 64:
            return
        occurrence = fs.occurrences[index]
        if occurrence.behaviour & PRODUCT_SUPPRESSED:
            return
        if occurrence.location is not None:
            matrix = multiply(matrix, occurrence.location)
        if occurrence.graphics.style >= 0:
            style = occurrence.graphics.style
        if not occurrence.graphics.behaviour & GRAPHICS_SHOW:
            visible = False
        part_fs, part_index, sons_fs, sons = fs, occurrence.part, fs, list(occurrence.sons)
        for link, link_fs in ((occurrence.prototype, occurrence.prototype_fs), (occurrence.external, occurrence.external_fs)):
            if link < 0:
                continue
            target_fs = self._structure(link_fs, fs)
            if target_fs is None:
                continue
            target_index = link
            if link_fs is not None and not 0 <= target_index < len(target_fs.occurrences):
                target_index = target_fs.root
            seen = 0
            while 0 <= target_index < len(target_fs.occurrences) and seen < 64:
                target = target_fs.occurrences[target_index]
                if part_index < 0 and target.part >= 0:
                    part_fs, part_index = target_fs, target.part
                if not sons and target.sons:
                    sons_fs, sons = target_fs, list(target.sons)
                if target.prototype < 0 or target.prototype_fs is not None:
                    break
                target_index = target.prototype
                seen += 1
        if 0 <= part_index < len(part_fs.parts):
            part = part_fs.parts[part_index]
            part_style = part.graphics.style if part.graphics.style >= 0 else style
            part_visible = visible and bool(part.graphics.behaviour & GRAPHICS_SHOW)
            label = part.name or occurrence.name
            for item in part.items:
                self._item(builder, part_fs, item, matrix, part_style, part_visible, label, used, 0)
        for son in sons:
            if 0 <= son < len(sons_fs.occurrences) and son != index:
                self._visit(builder, sons_fs, son, matrix, style, visible, used, depth + 1)

    def _item(self, builder, fs, item, matrix, style, visible, label, used, depth) -> None:
        if depth > 64:
            return
        if 0 <= item.coordinate_system < len(fs.coordinate_systems):
            matrix = multiply(matrix, fs.coordinate_systems[item.coordinate_system])
        if item.graphics.style >= 0:
            style = item.graphics.style
        if not item.graphics.behaviour & GRAPHICS_SHOW:
            visible = False
        name = item.name or label
        if item.kind == RI_SET:
            for child in item.children:
                self._item(builder, fs, child, matrix, style, visible, name, used, depth + 1)
            return
        if item.kind == RI_POINT_SET and item.points:
            builder.add_points(fs, item.points, name, matrix, style, visible)
            return
        if 0 <= item.tessellation < len(fs.tessellations):
            tess = fs.tessellations[item.tessellation]
            if tess is None or tess.kind == TESS_MARKUP:
                return
            used.add((id(fs), item.tessellation))
            builder.add_tessellation(fs, tess, name, matrix, style, visible)


# -- construcción de lotes -------------------------------------------------------------


class _SceneBuilder:
    def __init__(self, scene: Scene):
        self.scene = scene
        self._materials: dict[tuple, int] = {}
        self._cache: dict[tuple, list[int]] = {}

    def _material(self, fs: FileStructure, style_index: int | None, kind: str = "triangles") -> int:
        material = Material()
        if style_index is not None and 0 <= style_index < len(fs.styles):
            style = fs.styles[style_index]
            color = None
            opacity = 1.0
            if style.is_material and 0 <= style.index < len(fs.materials):
                definition = fs.materials[style.index]
                if definition.kind == "texture" and 0 <= definition.generic_index < len(fs.materials):
                    definition = fs.materials[definition.generic_index]
                if definition.kind == "generic":
                    if 0 <= definition.diffuse < len(fs.colors):
                        color = fs.colors[definition.diffuse]
                    elif 0 <= definition.ambient < len(fs.colors):
                        color = fs.colors[definition.ambient]
                    if 0 <= definition.specular < len(fs.colors):
                        material.specular = _clamp_color(fs.colors[definition.specular])
                    if 0 <= definition.emissive < len(fs.colors):
                        material.emissive = _clamp_color(fs.colors[definition.emissive])
                    material.shininess = _clamp(definition.shininess / 128.0 if definition.shininess > 1 else definition.shininess)
                    opacity = _clamp(definition.diffuse_alpha) if math.isfinite(definition.diffuse_alpha) else 1.0
            elif not style.is_material and 0 <= style.index < len(fs.colors):
                color = fs.colors[style.index]
            if color is not None:
                material.diffuse = _clamp_color(color)
            if style.transparency is not None:
                opacity = min(opacity, style.transparency / 255.0)
            material.opacity = opacity if opacity > 0.02 else 1.0
        if kind != "triangles":
            material.lighting = False
        key = (tuple(material.__dict__.values()))
        index = self._materials.get(key)
        if index is None:
            index = self._materials[key] = len(self.scene.materials)
            self.scene.materials.append(material)
        return index

    def add_points(self, fs, points, name, matrix, style, visible) -> None:
        primitive = build_points(points)
        primitive.material = self._material(fs, style, "points")
        self.scene.primitives.append(primitive)
        self.scene.parts.append(Part(name or "Puntos", matrix, [len(self.scene.primitives) - 1], visible))

    def add_tessellation(self, fs, tess, name, matrix, style, visible) -> None:
        key = (id(fs), id(tess), style)
        primitives = self._cache.get(key)
        if primitives is None:
            primitives = self._cache[key] = self._build(fs, tess, style)
        if primitives:
            self.scene.parts.append(Part(name or "Pieza", matrix, list(primitives), visible))

    def _build(self, fs, tess, style) -> list[int]:
        if tess.kind == TESS_3D_WIRE:
            return self._build_wire(fs, tess, style)
        if tess.kind != TESS_3D:
            return []
        coords = tess.coordinates
        positions = [(coords[i], coords[i + 1], coords[i + 2]) for i in range(0, len(coords) - 2, 3)]
        normals_src = tess.normals
        groups: dict[int | None, dict] = {}
        indices = tess.triangulated
        recalc = tess.recalculate_normals or not normals_src
        for face in tess.faces:
            face_styles = face.line_attributes
            triangles, corner_normals, corner_colors = _face_triangles(face, indices, recalc, len(positions), len(normals_src))
            if not triangles:
                continue
            if len(face_styles) > 1 and len(face_styles) != len(triangles):
                face_styles = face_styles[:1]
            for number, tri in enumerate(triangles):
                if len(face_styles) > 1:
                    group_style = face_styles[number]
                elif face_styles:
                    group_style = face_styles[0]
                else:
                    group_style = None
                if group_style is not None and group_style < 0:
                    group_style = None
                group = groups.setdefault(group_style, {"tris": [], "normals": [], "colors": []})
                group["tris"].append(tri)
                if corner_normals is not None:
                    for corner in range(3):
                        index = corner_normals[number * 3 + corner]
                        if index is None or index + 2 >= len(normals_src):
                            group["normals"].append(None)
                        else:
                            group["normals"].append((normals_src[index], normals_src[index + 1], normals_src[index + 2]))
                if face.colors is not None and corner_colors is not None:
                    for corner in range(3):
                        index = corner_colors[number * 3 + corner]
                        color = face.colors[index] if index < len(face.colors) else face.colors[-1]
                        group["colors"].append(tuple(c / 255.0 for c in color) + ((1.0,) if len(color) == 3 else ()))
        result = []
        for group_style, group in groups.items():
            effective = group_style if group_style is not None else style
            normals = group["normals"] if not recalc and len(group["normals"]) == len(group["tris"]) * 3 else None
            colors = group["colors"] if len(group["colors"]) == len(group["tris"]) * 3 else None
            primitive = build_triangles(positions, group["tris"], normals, colors)
            material = self._material(fs, effective)
            primitive.material = material
            if colors is not None:
                self.scene.materials[material].vertex_colors = True
            wire = _wire_edges(tess, positions)
            if wire:
                primitive.edges = wire
            result.append(len(self.scene.primitives))
            self.scene.primitives.append(primitive)
        return result

    def _build_wire(self, fs, tess, style) -> list[int]:
        coords = tess.coordinates
        positions = [(coords[i], coords[i + 1], coords[i + 2]) for i in range(0, len(coords) - 2, 3)]
        segments = []
        indices = tess.wire_indices
        if not indices:
            segments = [(i, i + 1) for i in range(len(positions) - 1)]
        else:
            k = 0
            previous_last = None
            while k < len(indices):
                raw = indices[k]
                count = raw & 0x0FFFFFFF
                points = [indices[k + 1 + j] // 3 for j in range(count) if k + 1 + j < len(indices)]
                k += 1 + count
                if raw & WIRE3D_CONTINUOUS and previous_last is not None and points:
                    segments.append((previous_last, points[0]))
                for a, b in zip(points, points[1:]):
                    segments.append((a, b))
                if raw & WIRE3D_CLOSING and len(points) > 2:
                    segments.append((points[-1], points[0]))
                if points:
                    previous_last = points[-1]
        count = len(positions)
        segments = [s for s in segments if s[0] < count and s[1] < count]
        if not segments:
            return []
        primitive = build_lines(positions, segments)
        primitive.material = self._material(fs, style, "lines")
        self.scene.primitives.append(primitive)
        return [len(self.scene.primitives) - 1]


def _face_triangles(face: TessFace, indices: list, recalc: bool, point_count: int, normal_count: int):
    """Triángulos de una cara: (índices de punto, índices de normal por esquina, color por esquina)."""
    triangles = []
    corner_normals = [] if not recalc else None
    corner_colors = []
    sizes = face.sizes_triangulated
    position = face.start_triangulated
    k = 0
    color_index = 0
    used = face.used_entities
    if used & FACE_POLYFACE_TYPES:
        return [], None, None
    textures = face.texture_indexes

    def take() -> int:
        nonlocal position
        value = indices[position]
        position += 1
        return value

    def vertex(with_normal: bool, normal_value):
        """Devuelve (punto, normal) leyendo normal (si hay), texturas y punto."""
        normal = normal_value
        if with_normal and not recalc:
            normal = take()
        for _ in range(texture_count):
            take()
        return take() // 3, normal

    for bit in (FACE_TRIANGLE, FACE_FAN, FACE_STRIPE, FACE_TRIANGLE_ONE_NORMAL, FACE_FAN_ONE_NORMAL,
                FACE_STRIPE_ONE_NORMAL, FACE_TRIANGLE_TEXTURED, FACE_FAN_TEXTURED, FACE_STRIPE_TEXTURED,
                FACE_TRIANGLE_ONE_NORMAL_TEXTURED, FACE_FAN_ONE_NORMAL_TEXTURED, FACE_STRIPE_ONE_NORMAL_TEXTURED):
        if not used & bit:
            continue
        if k >= len(sizes):
            break
        texture_count = textures if bit >= FACE_TRIANGLE_TEXTURED else 0
        one_normal = bit in (FACE_TRIANGLE_ONE_NORMAL, FACE_FAN_ONE_NORMAL, FACE_STRIPE_ONE_NORMAL,
                             FACE_TRIANGLE_ONE_NORMAL_TEXTURED, FACE_FAN_ONE_NORMAL_TEXTURED,
                             FACE_STRIPE_ONE_NORMAL_TEXTURED)
        is_triangle = bit in (FACE_TRIANGLE, FACE_TRIANGLE_ONE_NORMAL, FACE_TRIANGLE_TEXTURED,
                              FACE_TRIANGLE_ONE_NORMAL_TEXTURED)
        if is_triangle:
            count = sizes[k]
            k += 1
            for _ in range(count):
                if one_normal:
                    normal = None if recalc else take()
                    corners = [vertex(False, normal) for _ in range(3)]
                else:
                    corners = [vertex(True, None) for _ in range(3)]
                triangles.append(tuple(c[0] for c in corners))
                if corner_normals is not None:
                    corner_normals.extend(c[1] for c in corners)
                corner_colors.extend((color_index, color_index + 1, color_index + 2))
                color_index += 3
            continue
        entities = sizes[k]
        k += 1
        for _ in range(entities):
            if k >= len(sizes):
                break
            raw = sizes[k]
            k += 1
            count = raw & NORMAL_MASK
            single = bool(raw & NORMAL_SINGLE)
            if one_normal and single:
                normal = None if recalc else take()
                corners = [vertex(False, normal) for _ in range(count)]
            else:
                corners = [vertex(True, None) for _ in range(count)]
            base = color_index
            color_index += count
            for i in range(count - 2):
                if bit in (FACE_FAN, FACE_FAN_ONE_NORMAL, FACE_FAN_TEXTURED, FACE_FAN_ONE_NORMAL_TEXTURED):
                    order = (0, i + 1, i + 2)
                elif i % 2 == 0:
                    order = (i, i + 1, i + 2)
                else:
                    order = (i + 1, i, i + 2)
                triangles.append(tuple(corners[j][0] for j in order))
                if corner_normals is not None:
                    if one_normal and not single:
                        normal = corners[i + 2][1]
                        corner_normals.extend((normal, normal, normal))
                    else:
                        corner_normals.extend(corners[j][1] for j in order)
                corner_colors.extend(base + j for j in order)
    valid = []
    valid_normals = [] if corner_normals is not None else None
    valid_colors = []
    for number, tri in enumerate(triangles):
        if max(tri) >= point_count:
            continue
        valid.append(tri)
        if valid_normals is not None:
            normals = corner_normals[number * 3:number * 3 + 3]
            valid_normals.extend(n if n is not None and n + 2 < normal_count else None for n in normals)
        valid_colors.extend(corner_colors[number * 3:number * 3 + 3])
    return valid, valid_normals, valid_colors


def _wire_edges(tess: Tessellation, positions: list) -> bytes:
    """Aristas reales de las caras (bordes del B-rep) para el modo contorno."""
    from array import array

    wire = tess.wire_indices
    if not wire:
        return b""
    out = array("f")
    count = len(positions)
    for face in tess.faces:
        position = face.start_wire
        for raw in face.sizes_wire:
            points = raw & 0x3FFF
            chain = wire[position:position + points]
            position += points
            if raw & WIRE_NOT_DRAWN:
                continue
            for a, b in zip(chain, chain[1:]):
                a //= 3
                b //= 3
                if a < count and b < count:
                    out.extend(positions[a])
                    out.extend(positions[b])
    return out.tobytes()


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return min(1.0, max(0.0, value))


def _clamp_color(color) -> tuple[float, float, float]:
    return tuple(_clamp(c) for c in color)


def decode(data: bytes, progress: ProgressCallback | None = None) -> Scene:
    """Decodifica un archivo PRC completo."""
    return PRCFile(data, progress).scene()
