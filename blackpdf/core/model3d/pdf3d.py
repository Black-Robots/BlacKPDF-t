"""Modelos 3D incrustados en PDF: anotaciones 3D y RichMedia.

Localiza los modelos de cada página, extrae el flujo U3D o PRC y traduce
las vistas guardadas en el PDF (cámara, fondo, modo de dibujo) a
:class:`View3D`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pymupdf
from pymupdf import mupdf

from .scene import Matrix, Scene, View3D, is_finite_matrix, multiply

FORMATS = ("U3D", "PRC")

# La cámara de un PDF mira hacia +Z con Y arriba y X a la izquierda; en
# OpenGL mira hacia -Z con X a la derecha: se giran 180 grados sobre Y.
_PDF_TO_GL = (-1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, -1.0, 0.0, 0.0, 0.0, 0.0, 1.0)

RENDER_MODES = {
    "Solid": "solid",
    "SolidOutline": "outline",
    "Illustration": "outline",
    "SolidWireframe": "solid-wire",
    "ShadedWireframe": "solid-wire",
    "ShadedIllustration": "outline",
    "Transparent": "transparent",
    "TransparentWireframe": "transparent",
    "TransparentBoundingBox": "transparent",
    "TransparentBoundingBoxOutline": "transparent",
    "Wireframe": "wireframe",
    "HiddenWireframe": "wireframe",
    "BoundingBox": "wireframe",
    "Vertices": "wireframe",
    "ShadedVertices": "solid",
}


@dataclass
class Embedded3D:
    """Un modelo 3D colocado en una página."""

    page: int
    xref: int  # xref de la anotación
    kind: str  # "3D" o "RichMedia"
    format: str  # "U3D" o "PRC"
    rect: tuple[float, float, float, float]  # coordenadas de página sin girar
    name: str
    has_appearance: bool


# -- objetos PDF ------------------------------------------------------------------


def _pdf(doc: pymupdf.Document):
    return pymupdf._as_pdf_document(doc)


def _get(obj, key: str):
    value = mupdf.pdf_dict_gets(obj, key)
    return None if mupdf.pdf_is_null(value) else value


def _name(obj) -> str:
    return mupdf.pdf_to_name(obj) if obj is not None and mupdf.pdf_is_name(obj) else ""


def _text(obj) -> str:
    if obj is None:
        return ""
    if mupdf.pdf_is_string(obj):
        return mupdf.pdf_to_text_string(obj)
    if mupdf.pdf_is_name(obj):
        return mupdf.pdf_to_name(obj)
    return ""


def _numbers(obj) -> list[float]:
    if obj is None or not mupdf.pdf_is_array(obj):
        return []
    return [mupdf.pdf_to_real(mupdf.pdf_array_get(obj, i)) for i in range(mupdf.pdf_array_len(obj))]


def _items(obj) -> list:
    if obj is None or not mupdf.pdf_is_array(obj):
        return []
    return [mupdf.pdf_array_get(obj, i) for i in range(mupdf.pdf_array_len(obj))]


def _stream_bytes(obj) -> bytes:
    return bytes(mupdf.pdf_load_stream(obj).fz_buffer_extract())


def _page_box(page_obj) -> tuple[float, float, float, float]:
    media = _numbers(mupdf.pdf_dict_get_inheritable(page_obj, mupdf.pdf_new_name("MediaBox")))
    crop = _numbers(mupdf.pdf_dict_get_inheritable(page_obj, mupdf.pdf_new_name("CropBox")))
    box = crop if len(crop) == 4 else media
    if len(box) != 4:
        return 0.0, 0.0, 612.0, 792.0
    x0, y0, x1, y1 = box
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


# -- búsqueda ---------------------------------------------------------------------


def _three_d_stream(annot):
    """Flujo 3D de una anotación /3D (directo o mediante /3DRef)."""
    target = _get(annot, "3DD")
    if target is None:
        return None
    if not mupdf.pdf_is_stream(target):
        target = _get(target, "3DD")
    if target is None or not mupdf.pdf_is_stream(target):
        return None
    return target


def _rich_media_asset(annot):
    """Archivo 3D de una anotación RichMedia: (flujo, formato) o None."""
    content = _get(annot, "RichMediaContent")
    if content is None:
        return None
    for config in _items(_get(content, "Configurations")):
        if _name(_get(config, "Subtype")) != "3D":
            continue
        for instance in _items(_get(config, "Instances")):
            asset = _get(instance, "Asset")
            found = _filespec_stream(asset)
            if found is not None:
                return found
    assets = _get(content, "Assets")
    names = _items(_get(assets, "Names")) if assets is not None else []
    for index in range(0, len(names) - 1, 2):
        found = _filespec_stream(names[index + 1], _text(names[index]))
        if found is not None:
            return found
    return None


def _filespec_stream(spec, label: str = ""):
    if spec is None or not mupdf.pdf_is_dict(spec):
        return None
    name = label or _text(_get(spec, "UF")) or _text(_get(spec, "F"))
    ef = _get(spec, "EF")
    stream = None
    if ef is not None:
        stream = _get(ef, "F") or _get(ef, "UF")
    if stream is None or not mupdf.pdf_is_stream(stream):
        return None
    lower = name.lower()
    if lower.endswith(".u3d"):
        return stream, "U3D"
    if lower.endswith(".prc"):
        return stream, "PRC"
    subtype = _name(_get(stream, "Subtype")).upper()
    if subtype in FORMATS:
        return stream, subtype
    return None


def _has_appearance(annot) -> bool:
    ap = _get(annot, "AP")
    normal = _get(ap, "N") if ap is not None else None
    if normal is None:
        return False
    if mupdf.pdf_is_stream(normal):
        try:
            return len(_stream_bytes(normal).strip()) > 16
        except Exception:
            return False
    return mupdf.pdf_dict_len(normal) > 0 if mupdf.pdf_is_dict(normal) else False


def page_models(doc: pymupdf.Document, page_index: int) -> list[Embedded3D]:
    """Modelos 3D de una página (no decodifica nada)."""
    if not doc.is_pdf:
        return []
    try:
        pdf = _pdf(doc)
        page_obj = mupdf.pdf_lookup_page_obj(pdf, page_index)
    except Exception:
        return []
    annots = _get(page_obj, "Annots")
    if annots is None:
        return []
    x0, y0, x1, y1 = _page_box(page_obj)
    result = []
    for annot in _items(annots):
        if not mupdf.pdf_is_dict(annot):
            continue
        subtype = _name(_get(annot, "Subtype"))
        if subtype == "3D":
            stream = _three_d_stream(annot)
            if stream is None:
                continue
            fmt = _name(_get(stream, "Subtype")).upper()
            if fmt not in FORMATS:
                continue
        elif subtype == "RichMedia":
            found = _rich_media_asset(annot)
            if found is None:
                continue
            fmt = found[1]
        else:
            continue
        rect = _numbers(_get(annot, "Rect"))
        if len(rect) != 4:
            continue
        rx0, ry0, rx1, ry1 = min(rect[0], rect[2]), min(rect[1], rect[3]), max(rect[0], rect[2]), max(rect[1], rect[3])
        result.append(Embedded3D(
            page=page_index,
            xref=mupdf.pdf_to_num(annot),
            kind=subtype,
            format=fmt,
            rect=(rx0 - x0, y1 - ry1, rx1 - x0, y1 - ry0),
            name=_text(_get(annot, "Contents")) or _text(_get(annot, "NM")),
            has_appearance=_has_appearance(annot),
        ))
    return result


def find_models(doc: pymupdf.Document) -> list[Embedded3D]:
    result = []
    for index in range(doc.page_count):
        result.extend(page_models(doc, index))
    return result


# -- vistas -----------------------------------------------------------------------


def _c2w_camera(values: list[float]) -> Matrix | None:
    if len(values) != 12 or not all(math.isfinite(v) for v in values):
        return None
    a, b, c, d, e, f, g, h, i, tx, ty, tz = values
    matrix = (a, b, c, 0.0, d, e, f, 0.0, g, h, i, 0.0, tx, ty, tz, 1.0)
    camera = multiply(matrix, _PDF_TO_GL)
    return camera if is_finite_matrix(camera) else None


def _view_spec(obj) -> dict | None:
    """Copia en Python puro de un diccionario de vista 3D."""
    if obj is None or not mupdf.pdf_is_dict(obj):
        return None
    spec: dict = {
        "XN": _text(_get(obj, "XN")),
        "IN": _text(_get(obj, "IN")),
        "MS": _name(_get(obj, "MS")),
        "C2W": _numbers(_get(obj, "C2W")),
    }
    path = _get(obj, "U3DPath")
    if path is not None:
        spec["U3DPath"] = [_text(item) for item in _items(path)] if mupdf.pdf_is_array(path) else [_text(path)]
    orbit = _get(obj, "CO")
    if orbit is not None and mupdf.pdf_is_number(orbit):
        spec["CO"] = mupdf.pdf_to_real(orbit)
    projection = _get(obj, "P")
    if projection is not None and mupdf.pdf_is_dict(projection):
        spec["P"] = _name(_get(projection, "Subtype"))
        fov = _get(projection, "FOV")
        if fov is not None and mupdf.pdf_is_number(fov):
            spec["FOV"] = mupdf.pdf_to_real(fov)
    background = _get(obj, "BG")
    if background is not None and mupdf.pdf_is_dict(background):
        spec["BG"] = _numbers(_get(background, "C"))
    render = _get(obj, "RM")
    if render is not None and mupdf.pdf_is_dict(render):
        spec["RM"] = _name(_get(render, "Subtype"))
    lighting = _get(obj, "LS")
    if lighting is not None and mupdf.pdf_is_dict(lighting):
        spec["LS"] = _name(_get(lighting, "Subtype"))
    hidden = []
    for node in _items(_get(obj, "NA")):
        visible = _get(node, "V") if mupdf.pdf_is_dict(node) else None
        if visible is not None and mupdf.pdf_is_bool(visible) and not mupdf.pdf_to_bool(visible):
            hidden.append(_text(_get(node, "N")))
    spec["hidden"] = hidden
    return spec


def _selector_spec(obj):
    """Copia de un selector de vista (/DV, /3DV o /View)."""
    if obj is None:
        return None
    if mupdf.pdf_is_int(obj):
        return ("index", mupdf.pdf_to_int(obj))
    if mupdf.pdf_is_name(obj):
        return ("name", mupdf.pdf_to_name(obj))
    if mupdf.pdf_is_string(obj):
        return ("text", mupdf.pdf_to_text_string(obj))
    if mupdf.pdf_is_dict(obj):
        return ("view", _view_spec(obj))
    return None


def _view(spec: dict, scene: Scene, fallback_name: str) -> View3D | None:
    if not spec:
        return None
    view = View3D(name=spec.get("XN") or spec.get("IN") or fallback_name)
    if spec.get("MS") == "U3D":
        for target in spec.get("U3DPath", []):
            for candidate in scene.views:
                if candidate.name == target and candidate.camera is not None:
                    view.camera = candidate.camera
                    view.projection = candidate.projection
                    view.fov = candidate.fov
                    view.ortho_height = candidate.ortho_height
                    break
            if view.camera is not None:
                break
    if view.camera is None:
        view.camera = _c2w_camera(spec.get("C2W", []))
    orbit = spec.get("CO")
    if orbit is not None and math.isfinite(orbit) and orbit > 0:
        view.orbit = orbit
    if spec.get("P") == "O":
        view.projection = "orthographic"
    elif "FOV" in spec and 1.0 < spec["FOV"] < 179.0:
        view.fov = spec["FOV"]
    color = spec.get("BG") or []
    if len(color) == 3:
        view.background = tuple(min(1.0, max(0.0, c)) for c in color)
    elif len(color) == 1:
        view.background = (min(1.0, max(0.0, color[0])),) * 3
    if spec.get("RM"):
        view.render_mode = RENDER_MODES.get(spec["RM"])
    view.lighting = spec.get("LS") or None
    view.hidden = list(spec.get("hidden", []))
    return view


def _choose(selector, specs: list[dict], views: list[View3D], scene: Scene, current: int | None) -> int | None:
    if selector is None:
        return current
    kind, value = selector
    if kind == "index":
        return value if 0 <= value < len(views) else current
    if kind == "name":
        if value == "F":
            return 0 if views else current
        if value == "L":
            return len(views) - 1 if views else current
        return current
    if kind == "text":
        for index, spec in enumerate(specs):
            if value and value in (spec.get("IN"), spec.get("XN")):
                return index
        return current
    if kind == "view":
        view = _view(value, scene, "Vista inicial")
        if view is not None and view.camera is not None:
            for index, existing in enumerate(views):
                if existing.name == view.name and existing.camera == view.camera:
                    return index
            views.append(view)
            return len(views) - 1
    return current


def apply_views(scene: Scene, specs: list[dict], selectors: list) -> None:
    """Sustituye las vistas de la escena por las del PDF, si las hay."""
    views: list[View3D] = []
    for number, spec in enumerate(specs, 1):
        view = _view(spec, scene, f"Vista {number}")
        if view is not None:
            views.append(view)
    default = 0 if views else None
    for selector in selectors:
        default = _choose(selector, specs, views, scene, default)
    if views:
        named = {view.name for view in views}
        extra = [view for view in scene.views if view.name not in named and view.camera is not None]
        scene.views = views + extra
        scene.default_view = default
    elif scene.views:
        scene.default_view = 0
    if scene.default_view is not None and scene.default_view < len(scene.views):
        background = scene.views[scene.default_view].background
        if background is not None:
            scene.background = background


# -- carga -------------------------------------------------------------------------


@dataclass
class EmbeddedData:
    """Lo necesario para decodificar un modelo fuera del documento."""

    data: bytes
    format: str
    views: list[dict]
    selectors: list


def extract(doc: pymupdf.Document, xref: int) -> EmbeddedData:
    """Saca del PDF el archivo 3D y sus vistas (rápido; no decodifica)."""
    annot = mupdf.pdf_load_object(_pdf(doc), xref)
    subtype = _name(_get(annot, "Subtype"))
    if subtype == "3D":
        stream = _three_d_stream(annot)
        if stream is None:
            raise ValueError("sin flujo 3D")
        fmt = _name(_get(stream, "Subtype")).upper()
        views = [spec for spec in map(_view_spec, _items(_get(stream, "VA"))) if spec]
        selectors = [_selector_spec(_get(stream, "DV")), _selector_spec(_get(annot, "3DV"))]
        return EmbeddedData(_stream_bytes(stream), fmt, views, selectors)
    if subtype == "RichMedia":
        found = _rich_media_asset(annot)
        if found is None:
            raise ValueError("sin modelo 3D")
        stream, fmt = found
        content = _get(annot, "RichMediaContent")
        settings = _get(annot, "RichMediaSettings")
        activation = _get(settings, "Activation") if settings is not None else None
        views = [spec for spec in map(_view_spec, _items(_get(content, "Views") if content is not None else None)) if spec]
        selectors = [_selector_spec(_get(activation, "View"))] if activation is not None else []
        return EmbeddedData(_stream_bytes(stream), fmt, views, selectors)
    raise ValueError("no es una anotación 3D")


def decode(data: bytes, fmt: str, progress=None) -> Scene:
    if fmt == "U3D":
        from .u3d import decode as decode_u3d

        return decode_u3d(data, progress)
    return decode_prc(data, progress)


def decode_prc(data: bytes, progress=None) -> Scene:
    """PRC con nanoPRC si está disponible; si no, con el lector en Python."""
    from . import prc, prc_native

    if prc_native.available():
        try:
            scene = prc_native.decode(data, progress)
        except prc_native.NativePRCError:
            scene = None
        if scene is not None and not scene.is_empty():
            return scene
    return prc.decode(data, progress)


def decode_embedded(embedded: EmbeddedData, progress=None) -> Scene:
    scene = decode(embedded.data, embedded.format, progress)
    apply_views(scene, embedded.views, embedded.selectors)
    return scene
