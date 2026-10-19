"""Lectura de PRC con la biblioteca nativa blackprc (basada en nanoPRC).

nanoPRC entiende lo que el lector en Python no puede: la teselación muy
comprimida que exporta Acrobat/Tetra4D y la geometría exacta (B-rep) que
escriben Asymptote y los programas CAD. La biblioteca se compila desde
``native/prc``; si no está disponible, :func:`available` devuelve False y se
usa el lector en Python.
"""

from __future__ import annotations

import ctypes
import os
import struct
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

from ..progress import ProgressCallback, report
from .scene import Material, Part, Primitive, Scene, is_finite_matrix

_VERSION = 1
_TRIANGLES = 0
_LINES = 1
_VERTEX_COLORS = 1


class NativePRCError(Exception):
    """La biblioteca nativa no pudo leer el archivo."""


def _library_names() -> tuple[str, ...]:
    if sys.platform == "win32":
        return ("blackprc.dll",)
    if sys.platform == "darwin":
        return ("libblackprc.dylib",)
    return ("libblackprc.so",)


def _library_path() -> Path | None:
    folder = Path(__file__).resolve().parent.parent.parent / "native"
    for name in _library_names():
        path = folder / name
        if path.is_file():
            return path
    return None


_library = None
_library_checked = False


def _load():
    global _library, _library_checked
    if _library_checked:
        return _library
    _library_checked = True
    if os.environ.get("BLACKPDF_NO_NATIVE_PRC"):
        return None
    path = _library_path()
    if path is None:
        return None
    try:
        library = ctypes.CDLL(str(path))
        library.bprc_version.restype = ctypes.c_int
        library.bprc_version.argtypes = []
        if library.bprc_version() != _VERSION:
            return None
        library.bprc_load.restype = ctypes.c_int
        library.bprc_load.argtypes = [
            ctypes.c_char_p,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)),
            ctypes.POINTER(ctypes.c_size_t),
        ]
        library.bprc_free.restype = None
        library.bprc_free.argtypes = [ctypes.POINTER(ctypes.c_ubyte)]
    except (OSError, AttributeError):
        return None
    _library = library
    return library


def available() -> bool:
    """True si la biblioteca nativa está instalada y es compatible."""
    return _load() is not None


# -- rutas -------------------------------------------------------------------


def _windows_short_path(path: str) -> str:
    from ctypes import wintypes

    function = ctypes.windll.kernel32.GetShortPathNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    function.restype = wintypes.DWORD
    size = function(path, None, 0)
    if size == 0:
        return path
    target = ctypes.create_unicode_buffer(size)
    if function(path, target, size) == 0:
        return path
    return target.value


@contextmanager
def _c_path(path: str):
    """Ruta que fopen() de C puede abrir, también con nombres no ASCII en Windows."""
    if sys.platform != "win32":
        yield os.fsencode(path)
        return
    for candidate in (path, _windows_short_path(path)):
        try:
            encoded = candidate.encode("mbcs", "strict")
        except UnicodeEncodeError:
            continue
        if encoded.decode("mbcs") == candidate:
            yield encoded
            return
    # Último recurso: abrir por nombre relativo desde la propia carpeta.
    folder, name = os.path.split(path)
    previous = os.getcwd()
    os.chdir(folder)
    try:
        yield name.encode("mbcs", "replace")
    finally:
        os.chdir(previous)


# -- lectura -----------------------------------------------------------------


def _call(path: str) -> bytes:
    library = _load()
    if library is None:
        raise NativePRCError("biblioteca nativa no disponible")
    block = ctypes.POINTER(ctypes.c_ubyte)()
    size = ctypes.c_size_t(0)
    with _c_path(path) as c_path:
        code = library.bprc_load(c_path, ctypes.byref(block), ctypes.byref(size))
    if code != 0 or not block:
        reasons = {-1: "sin memoria", -2: "argumentos no válidos", -3: "el archivo PRC no se puede leer"}
        raise NativePRCError(reasons.get(code, f"error {code}"))
    try:
        return ctypes.string_at(block, size.value)
    finally:
        library.bprc_free(block)


def decode_file(path: str, progress: ProgressCallback | None = None) -> Scene:
    """Lee un archivo PRC del disco."""
    report(progress, 0, 2)
    blob = _call(path)
    report(progress, 1, 2)
    scene = parse(blob)
    report(progress, 2, 2)
    return scene


def decode(data: bytes, progress: ProgressCallback | None = None) -> Scene:
    """Lee un PRC en memoria (nanoPRC necesita un archivo: se usa uno temporal)."""
    handle, path = tempfile.mkstemp(prefix="blackpdf-", suffix=".prc")
    try:
        with os.fdopen(handle, "wb") as file:
            file.write(data)
        return decode_file(path, progress)
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


class _Reader:
    def __init__(self, blob: bytes):
        self.view = memoryview(blob)
        self.offset = 0

    def take(self, size: int) -> memoryview:
        end = self.offset + size
        if size < 0 or end > len(self.view):
            raise NativePRCError("bloque de datos truncado")
        chunk = self.view[self.offset:end]
        self.offset = end
        return chunk

    def u32(self) -> int:
        return struct.unpack_from("<I", self.take(4))[0]

    def floats(self, count: int) -> bytes:
        return bytes(self.take(count * 4))


def parse(blob: bytes) -> Scene:
    """Convierte el bloque binario de blackprc en una escena."""
    reader = _Reader(blob)
    if bytes(reader.take(4)) != b"BPRC":
        raise NativePRCError("bloque de datos no reconocido")
    if reader.u32() != _VERSION:
        raise NativePRCError("versión de la biblioteca nativa incompatible")
    scene = Scene(format="PRC")
    materials: dict[tuple, int] = {}

    def material_for(color: tuple, vertex_colors: bool, lines: bool) -> int:
        key = (color, vertex_colors, lines)
        index = materials.get(key)
        if index is None:
            index = materials[key] = len(scene.materials)
            scene.materials.append(
                Material(
                    diffuse=color[:3],
                    opacity=color[3],
                    lighting=not lines,
                    vertex_colors=vertex_colors,
                )
            )
        return index

    for _ in range(reader.u32()):
        name = bytes(reader.take(reader.u32())).decode("utf-8", "replace")
        matrix = struct.unpack_from("<16d", reader.take(128))
        visible = bool(reader.u32())
        primitives = []
        for _ in range(reader.u32()):
            kind = reader.u32()
            flags = reader.u32()
            color = tuple(round(value, 6) for value in struct.unpack_from("<4f", reader.take(16)))
            count = reader.u32()
            positions = reader.floats(count * 3)
            if kind == _LINES:
                primitive = Primitive(kind="lines", positions=positions)
                primitive.material = material_for(color, False, True)
            elif kind == _TRIANGLES:
                normals = reader.floats(count * 3)
                colors = reader.floats(count * 4) if flags & _VERTEX_COLORS else b""
                wire = reader.floats(reader.u32() * 3)
                edges = reader.floats(reader.u32() * 3)
                primitive = Primitive(
                    kind="triangles", positions=positions, normals=normals, colors=colors, wire=wire, edges=edges
                )
                primitive.material = material_for(color, bool(colors), False)
            else:
                raise NativePRCError(f"tipo de lote desconocido: {kind}")
            primitives.append(len(scene.primitives))
            scene.primitives.append(primitive)
        if not is_finite_matrix(matrix):
            continue
        scene.parts.append(Part(name=name, matrix=matrix, primitives=primitives, visible=visible))
    if reader.offset != len(reader.view):
        raise NativePRCError("datos sobrantes al final del bloque")
    _number_names(scene.parts)
    return scene


def _number_names(parts: list[Part]) -> None:
    """Nombres únicos para el panel de piezas: «Tornillo», «Tornillo (2)»..."""
    totals: dict[str, int] = {}
    for part in parts:
        totals[part.name] = totals.get(part.name, 0) + 1
    seen: dict[str, int] = {}
    for index, part in enumerate(parts, 1):
        base = part.name
        if not base:
            part.name = f"Pieza {index}"
            continue
        if totals[base] > 1:
            seen[base] = seen.get(base, 0) + 1
            if seen[base] > 1:
                part.name = f"{base} ({seen[base]})"
