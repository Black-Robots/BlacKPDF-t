"""Meter piezas 3D en un PDF: anotación 3D con un modelo PRC (o U3D), su vista y su portada.

``prepare`` hace el trabajo pesado (leer la pieza, convertirla a PRC y dibujar la
portada) y puede ir en un subproceso; ``insert_model`` solo crea los objetos del PDF.
La vista inicial es isométrica, como la del visor 3D, y la portada es la imagen que se
ve antes de activar el modelo y en los lectores sin 3D.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from pathlib import Path

import pymupdf

from ..errors import Model3DError
from .meshes import CAD_EXTENSIONS, MESH_EXTENSIONS, parts_scene, read_parts
from .prc_writer import write_prc
from .scene import Scene, transform_point

INSERTABLE_EXTENSIONS = MESH_EXTENSIONS + CAD_EXTENSIONS + (".prc", ".u3d")
FOV = 30.0
"""Ángulo de visión (grados) sobre el lado más corto del recuadro."""
ISO_DIRECTION = (-1.0, -1.0, 0.9)
"""Desde dónde mira la vista isométrica (Z arriba), igual que el visor 3D."""
POSTER_TRIANGLES = 250_000


@dataclass
class Prepared3D:
    """Modelo listo para meter en el PDF (se puede enviar entre procesos)."""

    data: bytes
    format: str
    name: str
    poster: bytes
    """JPEG de la portada."""
    poster_size: tuple[int, int]
    c2w: list[float]
    orbit: float
    background: tuple[float, float, float]
    triangles: int
    parts: int


# -- vista ----------------------------------------------------------------------------


def _normalize(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2]) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def default_view(bounds) -> tuple[list[float], float, tuple]:
    """Cámara isométrica que encuadra la pieza: (C2W del PDF, distancia al centro, ejes)."""
    lo, hi = bounds
    center = tuple((lo[i] + hi[i]) / 2 for i in range(3))
    radius = max(math.dist(lo, hi) / 2, 1e-6)
    distance = radius / math.sin(math.radians(FOV / 2)) * 1.08
    back = _normalize(ISO_DIRECTION)
    position = tuple(center[i] + back[i] * distance for i in range(3))
    # Cámara del PDF: mira hacia +Z, con Y arriba y X a la izquierda.
    forward = tuple(-c for c in back)
    up = _normalize(tuple(u - _dot((0.0, 0.0, 1.0), forward) * f for u, f in zip((0.0, 0.0, 1.0), forward)))
    left = _cross(up, forward)
    c2w = [*left, *up, *forward, *position]
    return c2w, distance, (left, up, forward, position)


# -- portada ----------------------------------------------------------------------------


_IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def _rotate(matrix, v):
    return (matrix[0] * v[0] + matrix[4] * v[1] + matrix[8] * v[2],
            matrix[1] * v[0] + matrix[5] * v[1] + matrix[9] * v[2],
            matrix[2] * v[0] + matrix[6] * v[1] + matrix[10] * v[2])


def _poster_items(scene: Scene):
    """Triángulos (con la normal suavizada) y aristas vivas de las piezas visibles, en el mundo."""
    from array import array

    for part in scene.parts:
        if not part.visible:
            continue
        moved = part.matrix != _IDENTITY
        for index in part.primitives:
            primitive = scene.primitives[index]
            if primitive.kind != "triangles":
                continue
            material = scene.materials[primitive.material] if 0 <= primitive.material < len(scene.materials) else None
            color = material.diffuse if material is not None else (0.72, 0.72, 0.75)
            values, normals, edges = array("f"), array("f"), array("f")
            values.frombytes(primitive.positions)
            normals.frombytes(primitive.normals)
            edges.frombytes(primitive.edges)
            smooth = len(normals) == len(values)
            for k in range(0, len(values) - 8, 9):
                corners = [(values[k + j], values[k + j + 1], values[k + j + 2]) for j in (0, 3, 6)]
                if smooth:
                    n = [normals[k + j] + normals[k + j + 3] + normals[k + j + 6] for j in range(3)]
                else:
                    a, b, c = corners
                    n = _cross((b[0] - a[0], b[1] - a[1], b[2] - a[2]), (c[0] - a[0], c[1] - a[1], c[2] - a[2]))
                if moved:
                    corners = [transform_point(part.matrix, p) for p in corners]
                    n = _rotate(part.matrix, n)
                yield "t", corners, _normalize(n), color
            for k in range(0, len(edges) - 5, 6):
                ends = [(edges[k], edges[k + 1], edges[k + 2]), (edges[k + 3], edges[k + 4], edges[k + 5])]
                if moved:
                    ends = [transform_point(part.matrix, p) for p in ends]
                yield "e", ends, None, color


def render_poster(scene: Scene, axes, size: tuple[int, int], background=(1.0, 1.0, 1.0)) -> bytes:
    """Dibuja la pieza desde la vista inicial: de atrás adelante, con luz principal, de relleno y aristas."""
    left, up, forward, position = axes
    width, height = size
    aspect = width / height
    half = math.tan(math.radians(FOV / 2))
    # El ángulo se aplica al lado corto, como en el PDF.
    tan_x, tan_y = (half * aspect, half) if aspect >= 1 else (half, half / aspect)
    # Luz principal desde arriba a la izquierda de la cámara y luz de relleno desde la cámara.
    key = _normalize(tuple(0.45 * left[i] + 0.65 * up[i] - forward[i] for i in range(3)))
    fill = tuple(-c for c in forward)
    items = []
    for kind, points, normal, color in _poster_items(scene):
        projected = []
        depth = 0.0
        for p in points:
            d = (p[0] - position[0], p[1] - position[1], p[2] - position[2])
            z = _dot(d, forward)
            if z <= 1e-9:
                break
            projected.append(((1 - _dot(d, left) / (z * tan_x)) / 2 * width,
                              (1 - _dot(d, up) / (z * tan_y)) / 2 * height))
            depth += z
        if len(projected) != len(points):
            continue
        depth /= len(points)
        if kind == "t":
            (x1, y1), (x2, y2), (x3, y3) = projected
            if abs((x2 - x1) * (y3 - y1) - (x3 - x1) * (y2 - y1)) < 0.04:
                continue
            light = 0.22 + 0.58 * abs(_dot(normal, key)) + 0.28 * abs(_dot(normal, fill))
            items.append((depth, 0, projected, tuple(min(1.0, ch * light) for ch in color)))
        else:
            # Las aristas, un poco por delante de sus caras para que no las tapen.
            items.append((depth * 0.9995, 1, projected, tuple(ch * 0.32 for ch in color)))
    if len(items) > POSTER_TRIANGLES:
        step = len(items) / POSTER_TRIANGLES
        items = [items[int(i * step)] for i in range(POSTER_TRIANGLES)]
    items.sort(key=lambda item: -item[0])
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.draw_rect(page.rect, color=None, fill=background)
    shape = page.new_shape()
    edge_width = max(1.0, min(width, height) / 700)
    for _, kind, points, color in items:
        if kind == 0:
            shape.draw_polyline([*points, points[0]])
            # El borde del mismo color tapa las rayitas claras entre triángulos vecinos.
            shape.finish(color=color, fill=color, width=0.6, closePath=True, lineJoin=1)
        else:
            shape.draw_line(*points)
            shape.finish(color=color, width=edge_width, lineCap=1)
    shape.commit()
    return page.get_pixmap(matrix=pymupdf.Matrix(1, 1)).tobytes("jpeg", jpg_quality=88)


def poster_size(aspect: float, longest: int = 1400) -> tuple[int, int]:
    aspect = max(0.2, min(5.0, aspect))
    return (longest, max(1, round(longest / aspect))) if aspect >= 1 else (max(1, round(longest * aspect)), longest)


# -- preparación --------------------------------------------------------------------------


def prepare(path: str, aspect: float = 4 / 3, background=(1.0, 1.0, 1.0), progress=None) -> Prepared3D:
    """Lee la pieza, la convierte a PRC si hace falta y dibuja la portada."""
    from . import detect_format, load_file

    extension = Path(path).suffix.lower()
    name = Path(path).name
    if extension in (".prc", ".u3d"):
        data = Path(path).read_bytes()
        fmt = detect_format(data)
        if fmt is None:
            raise Model3DError("formato desconocido", path=path)
        scene = load_file(path)
    elif extension in MESH_EXTENSIONS + CAD_EXTENSIONS:
        parts = read_parts(path, progress)
        data = write_prc(parts, Path(path).stem)
        fmt = "PRC"
        scene = parts_scene(parts, "PRC")
    else:
        raise Model3DError("formato no admitido", path=path)
    box = scene.bounds()
    if box is None:
        raise Model3DError("modelo vacío", path=path)
    c2w, orbit, axes = default_view(box)
    size = poster_size(aspect)
    poster = render_poster(scene, axes, size, background)
    return Prepared3D(data, fmt, name, poster, size, c2w, orbit, tuple(background),
                      scene.triangle_count, len(scene.parts))


# -- PDF ----------------------------------------------------------------------------------


def _pdf_text(text: str) -> str:
    """Cadena PDF (UTF-16 en hexadecimal: vale para cualquier nombre de archivo)."""
    return "<FEFF" + text.encode("utf-16-be").hex().upper() + ">"


def _number(value: float) -> str:
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return "0" if text in ("-0", "") else text


def _new_3d_annotation(page: pymupdf.Page) -> int:
    """Anotación 3D vacía creada con MuPDF: así también entra en su lista de anotaciones de la página."""
    from pymupdf import mupdf

    annot = mupdf.pdf_create_annot_raw(pymupdf._as_pdf_page(page), mupdf.PDF_ANNOT_3D)
    return mupdf.pdf_to_num(mupdf.pdf_annot_obj(annot))


def insert_model(doc: pymupdf.Document, page_index: int, rect, prepared: Prepared3D) -> int:
    """Crea la anotación 3D en ``rect`` (coordenadas visibles de la página). Devuelve su xref."""
    page = doc[page_index]
    visible = pymupdf.Rect(rect).normalize()
    if visible.is_empty:
        raise Model3DError("el recuadro está vacío")
    to_pdf = page.derotation_matrix * ~page.transformation_matrix
    pdf_rect = (visible * to_pdf).normalize()
    numbers = " ".join(_number(v) for v in (pdf_rect.x0, pdf_rect.y0, pdf_rect.x1, pdf_rect.y1))

    view = doc.get_new_xref()
    r, g, b = prepared.background
    doc.update_object(view, (
        f"<< /Type /3DView /XN {_pdf_text('Vista inicial')} /IN {_pdf_text('Vista inicial')} /MS /M "
        f"/C2W [{' '.join(_number(v) for v in prepared.c2w)}] /CO {_number(prepared.orbit)} "
        f"/P << /Subtype /P /PS /Min /FOV {_number(FOV)} >> "
        f"/BG << /Type /3DBG /Subtype /SC /CS /DeviceRGB /C [{_number(r)} {_number(g)} {_number(b)}] >> "
        f"/LS << /Type /3DLightingScheme /Subtype /CAD >> /RM << /Type /3DRenderMode /Subtype /Solid >> >>"))
    stream = doc.get_new_xref()
    doc.update_object(stream, f"<< /Type /3D /Subtype /{prepared.format} /VA [{view} 0 R] /DV 0 >>")
    doc.update_stream(stream, prepared.data, compress=True)

    # Portada: la imagen colocada para que se vea derecha aunque la página esté girada.
    width, height = prepared.poster_size
    image = doc.get_new_xref()
    doc.update_object(image, f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
                             f"/ColorSpace /DeviceRGB /BitsPerComponent 8 >>")
    doc.update_stream(image, prepared.poster, compress=False)
    doc.xref_set_key(image, "Filter", "/DCTDecode")
    bottom_left = pymupdf.Point(visible.x0, visible.y1) * to_pdf
    bottom_right = pymupdf.Point(visible.x1, visible.y1) * to_pdf
    top_left = pymupdf.Point(visible.x0, visible.y0) * to_pdf
    cm = (bottom_right - bottom_left, top_left - bottom_left, bottom_left)
    content = (f"q {_number(cm[0].x)} {_number(cm[0].y)} {_number(cm[1].x)} {_number(cm[1].y)} "
               f"{_number(cm[2].x)} {_number(cm[2].y)} cm /Im0 Do Q").encode()
    form = doc.get_new_xref()
    doc.update_object(form, f"<< /Type /XObject /Subtype /Form /BBox [{numbers}] "
                            f"/Resources << /XObject << /Im0 {image} 0 R >> >> >>")
    doc.update_stream(form, content, compress=True)

    annot = _new_3d_annotation(page)
    for key, value in (
        ("Rect", f"[{numbers}]"), ("P", f"{page.xref} 0 R"), ("F", "4"),
        ("Contents", _pdf_text(prepared.name)), ("NM", f"({uuid.uuid4().hex})"),
        ("3DD", f"{stream} 0 R"), ("3DV", f"{view} 0 R"), ("3DA", "<< /A /PO /D /PC >>"), ("3DI", "true"),
        ("AP", f"<< /N {form} 0 R >>"),
    ):
        doc.xref_set_key(annot, key, value)
    return annot


def new_page_rect(doc: pymupdf.Document, after: int) -> tuple[int, pymupdf.Rect]:
    """Añade una página detrás de ``after`` (del mismo tamaño) y devuelve el recuadro para el modelo."""
    reference = doc[max(0, min(after, doc.page_count - 1))].rect if doc.page_count else pymupdf.paper_rect("a4-l")
    index = after + 1 if doc.page_count else 0
    page = doc.new_page(index, width=reference.width, height=reference.height)
    margin = min(page.rect.width, page.rect.height) * 0.06
    return index, page.rect + (margin, margin, -margin, -margin)
