"""Lectura de modelos 3D (U3D y PRC) para el visor 3D.

Todo el paquete es Python puro: no depende de Qt ni de bibliotecas 3D
externas. Las funciones de carga son lentas con modelos grandes y deben
ejecutarse en un subproceso.
"""

from __future__ import annotations

from ..errors import FileOpenError, Model3DEmptyError, Model3DError
from ..progress import ProgressCallback
from .pdf3d import Embedded3D, EmbeddedData, find_models, page_models
from .scene import Material, Part, Primitive, Scene, View3D

__all__ = [
    "Embedded3D",
    "EmbeddedData",
    "Material",
    "Part",
    "Primitive",
    "Scene",
    "View3D",
    "MODEL_EXTENSIONS",
    "find_models",
    "extract_from_pdf",
    "load_embedded",
    "load_file",
    "page_models",
]

MODEL_EXTENSIONS = (".u3d", ".prc")


def _decode_errors():
    from .bitstream import BitStreamError
    from .prc import PRCError
    from .u3d import U3DError

    return (U3DError, PRCError, BitStreamError, ValueError, IndexError, KeyError, OverflowError, ZeroDivisionError)


def _checked(scene: Scene, path: str) -> Scene:
    if scene.is_empty():
        raise Model3DEmptyError(path=path)
    return scene


def detect_format(data: bytes) -> str | None:
    if data[:4] == b"U3D\0":
        return "U3D"
    if data[:3] == b"PRC":
        return "PRC"
    return None


def load_file(path: str, progress: ProgressCallback | None = None) -> Scene:
    """Lee un archivo .u3d o .prc suelto."""
    from .pdf3d import decode

    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as error:
        raise FileOpenError(str(error), path=path) from error
    fmt = detect_format(data)
    if fmt is None:
        raise Model3DError("formato desconocido", path=path)
    try:
        scene = decode(data, fmt, progress)
    except _decode_errors() as error:
        raise Model3DError(str(error), path=path) from error
    if scene.views:
        scene.default_view = 0
    return _checked(scene, path)


def extract_from_pdf(doc, xref: int) -> EmbeddedData:
    """Extrae del documento abierto el modelo de la anotación ``xref``."""
    from .pdf3d import extract

    try:
        return extract(doc, xref)
    except (ValueError, RuntimeError) as error:
        raise Model3DError(str(error)) from error


def load_embedded(embedded: EmbeddedData, name: str = "", progress: ProgressCallback | None = None) -> Scene:
    """Decodifica un modelo extraído con :func:`extract_from_pdf`."""
    from .pdf3d import decode_embedded

    try:
        scene = decode_embedded(embedded, progress)
    except _decode_errors() as error:
        raise Model3DError(str(error), path=name) from error
    return _checked(scene, name)
