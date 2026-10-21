"""Medir en planos: distancias, perímetros y áreas a escala.

Las medidas son anotaciones PDF estándar de medida (líneas, polilíneas y polígonos con
/IT y /Measure, como las de Acrobat), con la cifra dibujada junto a la figura. La escala
sale del propio PDF si la trae (/VP), de las medidas que ya tenga o de la que se elija.
Todas las coordenadas que recibe este módulo son visibles (`page.rect`).
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import pymupdf

from . import annotations as annots
from .errors import InvalidOptionError

PT_MM = 25.4 / 72
"""Milímetros que mide un punto PDF en el papel."""

UNITS_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "km": 1_000_000.0, "in": 25.4, "ft": 304.8, "yd": 914.4}
KINDS = ("distance", "perimeter", "area")
_INTENTS = {"distance": "LineDimension", "perimeter": "PolyLineDimension", "area": "PolygonDimension"}
_LABEL_KEY = "BPDFLabel"
"""Clave propia con el texto de la medida (/Contents queda para el comentario)."""
FONT_SIZE = 10.0


@dataclass(frozen=True)
class Scale:
    factor: float = PT_MM
    """Unidades reales por punto PDF."""
    unit: str = "mm"
    ratio: str = "1:1"

    @classmethod
    def from_ratio(cls, denominator: float, unit: str = "m") -> "Scale":
        """Escala de plano «1:denominador» medida en ``unit``."""
        if denominator <= 0 or unit not in UNITS_MM:
            raise InvalidOptionError(f"escala 1:{denominator} {unit}", option="scale")
        return cls(PT_MM * denominator / UNITS_MM[unit], unit, f"1:{_number(denominator)}")

    @classmethod
    def calibrated(cls, length_pt: float, real_length: float, unit: str) -> "Scale":
        """La que hace que una línea de ``length_pt`` puntos mida ``real_length``."""
        if length_pt <= 0 or real_length <= 0:
            raise InvalidOptionError("longitud no válida", option="length")
        factor = real_length / length_pt
        ratio = ""
        if unit in UNITS_MM:
            denominator = factor * UNITS_MM[unit] / PT_MM
            ratio = f"1:{_number(denominator)}"
        return cls(factor, unit, ratio)

    def length(self, points: float) -> float:
        return points * self.factor

    def area(self, square_points: float) -> float:
        return square_points * self.factor * self.factor

    def with_unit(self, unit: str) -> "Scale":
        """La misma escala expresada en otra unidad (de metros a centímetros, por ejemplo)."""
        if unit == self.unit or self.unit not in UNITS_MM or unit not in UNITS_MM:
            return self
        return Scale(self.factor * UNITS_MM[self.unit] / UNITS_MM[unit], unit, self.ratio)


def _number(value: float) -> str:
    return str(int(round(value))) if abs(value - round(value)) < 0.05 * max(1.0, value / 100) else f"{value:.2f}"


# ---------- geometría ----------
def path_length(points: Sequence[tuple[float, float]], closed: bool = False) -> float:
    pts = list(points) + (list(points[:1]) if closed and len(points) > 2 else [])
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip(pts, pts[1:]))


def polygon_area(points: Sequence[tuple[float, float]]) -> float:
    if len(points) < 3:
        return 0.0
    pts = list(points)
    return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))) / 2


def _centroid(points: Sequence[tuple[float, float]]) -> tuple[float, float]:
    pts = list(points)
    cross = [a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1])]
    area = sum(cross) / 2
    if abs(area) < 1e-9:
        return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)
    cx = sum((a[0] + b[0]) * c for (a, b), c in zip(zip(pts, pts[1:] + pts[:1]), cross)) / (6 * area)
    cy = sum((a[1] + b[1]) * c for (a, b), c in zip(zip(pts, pts[1:] + pts[:1]), cross)) / (6 * area)
    return cx, cy


def measure(kind: str, points: Sequence[tuple[float, float]], scale: Scale) -> float:
    """Valor real: longitud (distancia, perímetro) o superficie (área) en las unidades de la escala."""
    if kind == "distance":
        return scale.length(path_length(points[:2]))
    if kind == "perimeter":
        return scale.length(path_length(points))
    if kind == "area":
        return scale.area(polygon_area(points))
    raise InvalidOptionError(kind, option="kind")


# ---------- escala del documento ----------
def _unit_name(text: str) -> str:
    unit = text.strip().lower().replace(".", "")
    aliases = {"metros": "m", "metro": "m", "meters": "m", "centimetros": "cm", "milimetros": "mm", "inch": "in",
               "inches": "in", "pulg": "in", "\"": "in", "feet": "ft", "foot": "ft", "pies": "ft", "'": "ft"}
    return aliases.get(unit, unit)


def _scale_from_measure(measure_obj) -> Scale | None:
    from pymupdf import mupdf

    if not measure_obj.m_internal or not mupdf.pdf_is_dict(measure_obj):
        return None
    axis = mupdf.pdf_dict_gets(measure_obj, "X")
    if not axis.m_internal or not mupdf.pdf_array_len(axis):
        return None
    first = mupdf.pdf_array_get(axis, 0)
    factor = mupdf.pdf_to_real(mupdf.pdf_dict_gets(first, "C"))
    unit = _unit_name(mupdf.pdf_to_text_string(mupdf.pdf_dict_gets(first, "U")))
    if factor <= 0 or not unit:
        return None
    return Scale(factor, unit, mupdf.pdf_to_text_string(mupdf.pdf_dict_gets(measure_obj, "R")).strip())


def page_scale(page: pymupdf.Page, point: tuple[float, float] | None = None) -> Scale | None:
    """Escala que trae la página: la de su zona de dibujo (/VP) o la de sus medidas; None si no hay."""
    from pymupdf import mupdf

    doc = page.parent
    pdf = pymupdf._as_pdf_document(doc)
    page_obj = mupdf.pdf_lookup_page_obj(pdf, page.number)
    viewports = mupdf.pdf_dict_gets(page_obj, "VP")
    found: list[tuple[pymupdf.Rect, Scale]] = []
    if viewports.m_internal and mupdf.pdf_is_array(viewports):
        height = page.mediabox.height
        for index in range(mupdf.pdf_array_len(viewports)):
            viewport = mupdf.pdf_array_get(viewports, index)
            scale = _scale_from_measure(mupdf.pdf_dict_gets(viewport, "Measure"))
            if scale is None:
                continue
            box = mupdf.pdf_to_rect(mupdf.pdf_dict_gets(viewport, "BBox"))
            rect = pymupdf.Rect(box.x0, height - box.y1, box.x1, height - box.y0) * page.rotation_matrix
            found.append((rect, scale))
    if found:
        if point is not None:
            for rect, scale in found:
                if rect.contains(pymupdf.Point(point)):
                    return scale
        return max(found, key=lambda item: abs(item[0]))[1]
    for annot in reversed(list(page.annots() or [])):
        if is_measurement(annot):
            scale = _scale_from_measure(mupdf.pdf_dict_gets(mupdf.pdf_annot_obj(annot.this), "Measure"))
            if scale is not None:
                return scale
    return None


# ---------- ajuste a lo dibujado ----------
def snap_targets(page: pymupdf.Page, limit: int = 60_000) -> list[tuple[float, float]]:
    """Extremos de líneas y esquinas de lo dibujado en la página, para que las medidas caigan justo ahí."""
    try:
        drawings = page.get_cdrawings()
    except Exception:
        return []
    seen: set[tuple[float, float]] = set()
    matrix = page.rotation_matrix
    for drawing in drawings:
        for item in drawing.get("items", ()):
            kind = item[0]
            if kind == "l":
                raw = (item[1], item[2])
            elif kind == "c":
                raw = (item[1], item[4])
            elif kind == "re":
                r = pymupdf.Rect(item[1])
                raw = (r.tl, r.tr, r.br, r.bl)
            elif kind == "qu":
                q = pymupdf.Quad(item[1])
                raw = (q.ul, q.ur, q.lr, q.ll)
            else:
                continue
            for value in raw:
                p = pymupdf.Point(value) * matrix
                seen.add((round(p.x, 2), round(p.y, 2)))
            if len(seen) >= limit:
                return list(seen)
    return list(seen)


# ---------- anotaciones ----------
def is_measurement(annot: pymupdf.Annot) -> bool:
    try:
        return annot.parent.parent.xref_get_key(annot.xref, "IT")[1].lstrip("/") in _INTENTS.values()
    except Exception:
        return False


def _measure_dict(scale: Scale) -> str:
    unit = scale.unit
    number = "/Type/NumberFormat/D 100/F/D"
    return (f"<</Type/Measure/Subtype/RL/R{annots.pdf_string(scale.ratio or unit).decode('latin-1')}"
            f"/X[<<{number}/U{annots.pdf_string(unit).decode('latin-1')}/C {scale.factor:.10g}>>]"
            f"/D[<<{number}/U{annots.pdf_string(unit).decode('latin-1')}/C 1>>]"
            f"/A[<<{number}/U{annots.pdf_string(unit + '²').decode('latin-1')}/C 1>>]>>")


def add_measurement(
    page: pymupdf.Page,
    kind: str,
    points: Sequence[tuple[float, float]],
    scale: Scale,
    label: str,
    color: annots.Color = (0.05, 0.45, 0.81),
    width: float = 1.2,
) -> pymupdf.Annot:
    """Medida como anotación: ``label`` es la cifra ya escrita como la quiere ver el usuario («4,25 m»)."""
    if kind not in KINDS:
        raise InvalidOptionError(kind, option="kind")
    pts = [pymupdf.Point(p) for p in points]
    needed = 2 if kind == "distance" else 3 if kind == "area" else 2
    if len(pts) < needed or (kind == "distance" and abs(pts[1] - pts[0]) < 1) or (
            kind == "area" and polygon_area(points) < 1):
        raise InvalidOptionError("medida demasiado pequeña", option="points")
    unrotated = [p * page.derotation_matrix for p in pts]
    if kind == "distance":
        annot = page.add_line_annot(unrotated[0], unrotated[1])
    elif kind == "perimeter":
        annot = page.add_polyline_annot([(p.x, p.y) for p in unrotated])
    else:
        annot = page.add_polygon_annot([(p.x, p.y) for p in unrotated])
    annot.set_colors(stroke=color)
    annot.set_border(width=max(0.25, width))
    annot.set_info(content=label)
    annots._finish(annot)
    doc = page.parent
    doc.xref_set_key(annot.xref, "IT", "/" + _INTENTS[kind])
    doc.xref_set_key(annot.xref, "Measure", _measure_dict(scale))
    doc.xref_set_key(annot.xref, _LABEL_KEY, annots.pdf_string(label).decode("latin-1"))
    redraw(page, annot)
    return annot


def measurement_label(annot: pymupdf.Annot) -> str:
    doc = annot.parent.parent
    kind, value = doc.xref_get_key(annot.xref, _LABEL_KEY)
    if kind == "string":
        return value
    return (annot.info or {}).get("content", "")


def _visual_points(page: pymupdf.Page, annot: pymupdf.Annot) -> list[pymupdf.Point]:
    vertices = annot.vertices or []
    return [pymupdf.Point(v) * page.rotation_matrix for v in vertices]


def redraw(page: pymupdf.Page, annot: pymupdf.Annot) -> None:
    """Dibuja la figura con su cifra. Hay que llamarla después de cada update(), que la borra."""
    intent = page.parent.xref_get_key(annot.xref, "IT")[1].lstrip("/")
    kind = {v: k for k, v in _INTENTS.items()}.get(intent)
    if kind is None:
        return
    points = _visual_points(page, annot)
    if len(points) < 2:
        return
    stroke = (annot.colors or {}).get("stroke") or [0.05, 0.45, 0.81]
    width = float((annot.border or {}).get("width") or 1.2)
    _write_appearance(page, annot, kind, points, measurement_label(annot), tuple(stroke[:3]), max(width, 0.25))


def _write_appearance(page, annot, kind: str, points: list[pymupdf.Point], label: str, color, width: float) -> None:
    doc = page.parent
    to_pdf = page.derotation_matrix * ~page.transformation_matrix
    rgb = " ".join(f"{c:.3f}" for c in color)

    def pdf(point: pymupdf.Point) -> pymupdf.Point:
        return pymupdf.Point(point) * to_pdf

    def path(pts: list[pymupdf.Point], close: bool) -> str:
        first, *rest = [pdf(p) for p in pts]
        ops = [f"{first.x:.2f} {first.y:.2f} m"] + [f"{p.x:.2f} {p.y:.2f} l" for p in rest]
        return " ".join(ops) + (" h" if close else "")

    ops = ["q", f"{rgb} RG", f"{rgb} rg", f"{width:.2f} w", "1 J", "1 j"]
    extent = [pdf(p) for p in points]
    if kind == "area":
        ops += ["q /G1 gs", path(points, True), "f Q", path(points, True), "S"]
    else:
        ops += [path(points, False), "S"]
    if kind == "distance":
        a, b = points[0], points[1]
        direction = (b - a) / max(abs(b - a), 1e-9)
        normal = pymupdf.Point(direction.y, -direction.x) * 4
        for end in (a, b):
            tick = [end - normal, end + normal]
            ops += [path(tick, False), "S"]
            extent += [pdf(p) for p in tick]
    # La cifra, derecha según se ve la página, sobre una etiqueta blanca para que se lea encima del plano.
    size = FONT_SIZE
    text_width = annots.text_width(label, "helv", size)
    if kind == "distance":
        a, b = points[0], points[1]
        direction = (b - a) / max(abs(b - a), 1e-9)
        if direction.x < -1e-6 or (abs(direction.x) <= 1e-6 and direction.y > 0):
            direction = -direction  # que nunca quede boca abajo
        up = pymupdf.Point(direction.y, -direction.x)
        middle = (a + b) / 2
        origin = middle - direction * (text_width / 2) + up * (width / 2 + size * 0.55 + 1.5)
    else:
        direction, up = pymupdf.Point(1, 0), pymupdf.Point(0, -1)
        if kind == "area":
            cx, cy = _centroid([(p.x, p.y) for p in points])
            origin = pymupdf.Point(cx - text_width / 2, cy + size * 0.35)
        else:
            last = points[-1]
            origin = pymupdf.Point(last.x + 6, last.y - 6)
    base = pdf(origin)
    along = pdf(origin + direction) - base
    upward = pdf(origin + up) - base
    pad = size * 0.3
    x0, y0, x1, y1 = -pad, -size * 0.25 - pad, text_width + pad, size * 0.75 + pad
    ops += [f"q {along.x:.6f} {along.y:.6f} {upward.x:.6f} {upward.y:.6f} {base.x:.2f} {base.y:.2f} cm",
            "q /G2 gs 1 1 1 rg", annots.round_rect_path(x0, y0, x1, y1, (y1 - y0) * 0.3), "f Q",
            f"{rgb} rg"]
    stream = ("\n".join(ops) + "\n").encode() + b"BT /F1 %.2f Tf 0 0 Td " % size + annots.pdf_string(label) + b" Tj ET\nQ\nQ\n"
    for corner in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
        extent.append(base + along * corner[0] + upward * corner[1])
    margin = width + 2
    left = min(p.x for p in extent) - margin
    bottom = min(p.y for p in extent) - margin
    right = max(p.x for p in extent) + margin
    top = max(p.y for p in extent) + margin
    annot._setAP(stream)
    ap = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    doc.xref_set_key(annot.xref, "Rect", f"[{left:.2f} {bottom:.2f} {right:.2f} {top:.2f}]")
    doc.xref_set_key(ap, "BBox", f"[{left:.2f} {bottom:.2f} {right:.2f} {top:.2f}]")
    doc.xref_set_key(ap, "Matrix", "[1 0 0 1 0 0]")
    doc.xref_set_key(ap, "Resources", (
        # La fuente va dentro: cada vez que se redibuja no queda un objeto suelto más en el archivo.
        "<</Font<</F1<</Type/Font/Subtype/Type1/BaseFont/Helvetica/Encoding/WinAnsiEncoding>>>>"
        "/ExtGState<</G1<</Type/ExtGState/ca 0.12>>/G2<</Type/ExtGState/ca 0.88>>>>>>"))
