"""Anotaciones PDF estándar: lápiz, marcado de texto, formas, polígonos, sellos, texto, notas y firma.

Todas las funciones reciben coordenadas *visibles* (las de `page.rect`, tal
como se ve la página ya girada) y las convierten al espacio sin rotar que
usa PDF. Así la interfaz no tiene que saber nada de la rotación.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

import pymupdf

from .errors import InvalidOptionError

Color = tuple[float, float, float]
PointLike = pymupdf.Point | tuple[float, float]

MARKUP_KINDS = ("highlight", "underline", "strikeout", "squiggly")
SHAPE_KINDS = ("rect", "ellipse", "line", "arrow")
AUTHOR = "BlackPDF"


def _to_page(page: pymupdf.Page, point: PointLike) -> pymupdf.Point:
    return pymupdf.Point(point) * page.derotation_matrix


def _rect_to_page(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    return pymupdf.Rect(rect).normalize() * page.derotation_matrix


def to_visual_rect(page: pymupdf.Page, rect: pymupdf.Rect) -> pymupdf.Rect:
    """Rectángulo sin rotar (PDF) -> coordenadas visibles."""
    return pymupdf.Rect(rect) * page.rotation_matrix


_author = AUTHOR


def set_default_author(name: str) -> None:
    """Nombre que se guarda como autor de las anotaciones nuevas."""
    global _author
    _author = name.strip() or AUTHOR


def default_author() -> str:
    return _author


def _finish(annot: pymupdf.Annot, opacity: float = 1.0) -> pymupdf.Annot:
    if opacity < 1.0:
        annot.set_opacity(max(0.05, opacity))
    now = pymupdf.get_pdf_now()
    annot.set_info(title=_author, creationDate=now, modDate=now)
    annot.update()
    return annot


def add_ink(
    page: pymupdf.Page,
    strokes: Sequence[Sequence[PointLike]],
    color: Color = (0.0, 0.0, 0.0),
    width: float = 2.0,
    opacity: float = 1.0,
) -> pymupdf.Annot:
    """Trazo a mano alzada. Cada trazo es una lista de puntos."""
    paths = []
    for stroke in strokes:
        points = [_to_page(page, p) for p in stroke]
        if len(points) == 1:
            points.append(points[0] + (0.01, 0.01))
        if points:
            paths.append([(p.x, p.y) for p in points])
    if not paths:
        raise InvalidOptionError("trazo vacío", option="strokes")
    annot = page.add_ink_annot(paths)
    annot.set_colors(stroke=color)
    annot.set_border(width=max(0.25, width))
    return _finish(annot, opacity)


def add_text_markup(
    page: pymupdf.Page,
    kind: str,
    rects: Sequence[pymupdf.Rect],
    color: Color,
    opacity: float = 1.0,
) -> pymupdf.Annot:
    """Resaltado, subrayado, tachado u ondulado sobre los rectángulos dados.

    Los rectángulos suelen ser las líneas de texto seleccionadas; si la
    página no tiene texto (un escaneo) sirve el rectángulo arrastrado.
    """
    if kind not in MARKUP_KINDS:
        raise InvalidOptionError(kind, option="kind")
    quads = [_rect_to_page(page, r).quad for r in rects if not pymupdf.Rect(r).is_empty]
    if not quads:
        raise InvalidOptionError("sin texto seleccionado", option="rects")
    method = {
        "highlight": page.add_highlight_annot,
        "underline": page.add_underline_annot,
        "strikeout": page.add_strikeout_annot,
        "squiggly": page.add_squiggly_annot,
    }[kind]
    annot = method(quads=quads)
    annot.set_colors(stroke=color)
    return _finish(annot, opacity)


def add_shape(
    page: pymupdf.Page,
    kind: str,
    start: PointLike,
    end: PointLike,
    color: Color = (0.898, 0.282, 0.302),
    width: float = 2.0,
    fill: Color | None = None,
    opacity: float = 1.0,
) -> pymupdf.Annot:
    if kind not in SHAPE_KINDS:
        raise InvalidOptionError(kind, option="kind")
    a, b = _to_page(page, start), _to_page(page, end)
    if kind in ("rect", "ellipse"):
        rect = pymupdf.Rect(a, b).normalize()
        if rect.width < 1 or rect.height < 1:
            raise InvalidOptionError("forma demasiado pequeña", option="size")
        annot = page.add_rect_annot(rect) if kind == "rect" else page.add_circle_annot(rect)
        annot.set_colors(stroke=color, fill=fill)
    else:
        if abs(a - b) < 1:
            raise InvalidOptionError("línea demasiado corta", option="size")
        annot = page.add_line_annot(a, b)
        if kind == "arrow":
            annot.set_line_ends(pymupdf.PDF_ANNOT_LE_NONE, pymupdf.PDF_ANNOT_LE_CLOSED_ARROW)
            annot.set_colors(stroke=color, fill=color)
        else:
            annot.set_colors(stroke=color)
    annot.set_border(width=max(0.25, width))
    return _finish(annot, opacity)


def text_box_size(text: str, font_size: float) -> tuple[float, float]:
    """Tamaño aproximado (visible) que necesita un cuadro de texto."""
    lines = text.splitlines() or [""]
    width = max(pymupdf.get_text_length(line, fontname="helv", fontsize=font_size) for line in lines)
    padding = font_size * 0.6
    return width + 2 * padding + font_size * 0.5, len(lines) * font_size * 1.25 + 2 * padding


def add_text_box(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    text: str,
    font_size: float = 12.0,
    color: Color = (0.0, 0.0, 0.0),
    fill: Color | None = None,
    border: Color | None = None,
) -> pymupdf.Annot:
    if not text.strip():
        raise InvalidOptionError("texto vacío", option="text")
    annot = page.add_freetext_annot(
        _rect_to_page(page, rect),
        text,
        fontsize=font_size,
        fontname="helv",
        text_color=color,
        fill_color=fill,
        border_color=border,
        border_width=1 if border else 0,
        rotate=page.rotation,
    )
    return _finish(annot)


def add_note(
    page: pymupdf.Page,
    point: PointLike,
    text: str,
    color: Color = (1.0, 0.78, 0.2),
) -> pymupdf.Annot:
    annot = page.add_text_annot(_to_page(page, point), text, icon="Note")
    annot.set_colors(stroke=color)
    return _finish(annot)


Stroke = Sequence[tuple[float, float]]


def normalize_strokes(strokes: Sequence[Stroke]) -> list[list[tuple[float, float]]]:
    """Lleva los trazos de una firma a la caja unidad [0, 1] conservando la proporción."""
    points = [p for stroke in strokes for p in stroke]
    if not points:
        raise InvalidOptionError("firma vacía", option="strokes")
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    x0, y0 = min(xs), min(ys)
    span = max(max(xs) - x0, max(ys) - y0, 1e-6)
    return [[((x - x0) / span, (y - y0) / span) for x, y in stroke] for stroke in strokes if stroke]


def strokes_aspect(strokes: Sequence[Stroke]) -> float:
    """Ancho/alto de unos trazos normalizados."""
    points = [p for stroke in strokes for p in stroke]
    if not points:
        return 1.0
    width = max(p[0] for p in points) - min(p[0] for p in points)
    height = max(p[1] for p in points) - min(p[1] for p in points)
    return max(width, 1e-3) / max(height, 1e-3)


def add_signature(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    strokes: Sequence[Stroke],
    color: Color = (0.05, 0.1, 0.35),
    width: float = 1.6,
) -> pymupdf.Annot:
    """Estampa una firma (trazos normalizados) ajustada dentro de `rect`."""
    unit = normalize_strokes(strokes)
    xs = [p[0] for s in unit for p in s]
    ys = [p[1] for s in unit for p in s]
    w_unit = max(max(xs), 1e-3)
    h_unit = max(max(ys), 1e-3)
    rect = pymupdf.Rect(rect).normalize()
    scale = min(rect.width / w_unit, rect.height / h_unit)
    off_x = rect.x0 + (rect.width - w_unit * scale) / 2
    off_y = rect.y0 + (rect.height - h_unit * scale) / 2
    placed = [[(off_x + x * scale, off_y + y * scale) for x, y in s] for s in unit]
    return add_ink(page, placed, color=color, width=width)


def add_polygon(
    page: pymupdf.Page,
    points: Sequence[PointLike],
    color: Color = (0.898, 0.282, 0.302),
    width: float = 2.0,
    fill: Color | None = None,
    opacity: float = 1.0,
) -> pymupdf.Annot:
    """Polígono cerrado con los vértices dados (al menos tres)."""
    vertices = [_to_page(page, p) for p in points]
    # Los vértices repetidos (doble clic al terminar) sobran.
    unique = [v for i, v in enumerate(vertices) if i == 0 or abs(v - vertices[i - 1]) >= 0.5]
    if len(unique) > 2 and abs(unique[0] - unique[-1]) < 0.5:
        unique.pop()
    area = sum(a.x * b.y - b.x * a.y for a, b in zip(unique, unique[1:] + unique[:1])) / 2
    if len(unique) < 3 or abs(area) < 1:
        raise InvalidOptionError("hacen falta tres puntos que no estén en línea", option="points")
    annot = page.add_polygon_annot([(v.x, v.y) for v in unique])
    annot.set_colors(stroke=color, fill=fill)
    annot.set_border(width=max(0.25, width))
    return _finish(annot, opacity)


# ---------- sellos ----------
STAMP_FONT_SIZE = 20.0
"""Tamaño del texto principal de un sello a su tamaño natural."""

def _pdf_text(text: str) -> bytes:
    """Cadena PDF en WinAnsi (las fuentes estándar): lo que no se puede escribir sale como «?»."""
    raw = text.encode("cp1252", "replace")
    out = bytearray(b"(")
    for byte in raw:
        if byte in b"()\\":
            out += b"\\" + bytes([byte])
        elif 32 <= byte < 127:
            out.append(byte)
        else:
            out += b"\\%03o" % byte
    return bytes(out + b")")


def _round_rect(x0: float, y0: float, x1: float, y1: float, radius: float) -> str:
    r = max(0.0, min(radius, (x1 - x0) / 2, (y1 - y0) / 2))
    k = r * 0.5523
    return (f"{x0 + r:.2f} {y0:.2f} m {x1 - r:.2f} {y0:.2f} l "
            f"{x1 - r + k:.2f} {y0:.2f} {x1:.2f} {y0 + r - k:.2f} {x1:.2f} {y0 + r:.2f} c "
            f"{x1:.2f} {y1 - r:.2f} l {x1:.2f} {y1 - r + k:.2f} {x1 - r + k:.2f} {y1:.2f} {x1 - r:.2f} {y1:.2f} c "
            f"{x0 + r:.2f} {y1:.2f} l {x0 + r - k:.2f} {y1:.2f} {x0:.2f} {y1 - r + k:.2f} {x0:.2f} {y1 - r:.2f} c "
            f"{x0:.2f} {y0 + r:.2f} l {x0:.2f} {y0 + r - k:.2f} {x0 + r - k:.2f} {y0:.2f} {x0 + r:.2f} {y0:.2f} c h")


def _text_width(text: str, fontname: str, size: float) -> float:
    # Letra a letra: con la cadena entera, PyMuPDF mide mal las letras con tilde.
    return sum(pymupdf.get_text_length(char, fontname=fontname, fontsize=size) for char in text)


def _stamp_layout(text: str, subtitle: str) -> dict:
    fs = STAMP_FONT_SIZE
    spacing = fs * 0.06
    main = _text_width(text, "hebo", fs) + spacing * max(len(text) - 1, 0)
    small = fs * 0.42
    sub = _text_width(subtitle, "helv", small) if subtitle else 0.0
    width = max(main, sub) + 2 * fs * 0.85
    height = fs * (2.75 if subtitle else 2.0)
    return {"fs": fs, "spacing": spacing, "main": main, "small": small, "sub": sub, "width": width, "height": height}


def stamp_size(text: str, subtitle: str = "") -> tuple[float, float]:
    """Tamaño natural (en puntos) de un sello."""
    layout = _stamp_layout(text.strip().upper(), subtitle.strip())
    return layout["width"], layout["height"]


def _stamp_appearance(text: str, subtitle: str, color: Color) -> tuple[bytes, float, float]:
    layout = _stamp_layout(text, subtitle)
    fs, width, height = layout["fs"], layout["width"], layout["height"]
    line = fs * 0.11
    rgb = " ".join(f"{c:.3f}" for c in color)
    inner = line * 2.3
    ops = [
        "q", f"{rgb} RG", f"{rgb} rg", f"{line:.2f} w",
        _round_rect(line / 2, line / 2, width - line / 2, height - line / 2, fs * 0.38), "S",
        f"{line * 0.32:.2f} w",
        _round_rect(inner, inner, width - inner, height - inner, fs * 0.26), "S",
    ]
    baseline = fs * 1.37 if subtitle else fs * 0.64
    stream = ("\n".join(ops) + "\n").encode()
    stream += (f"BT /F1 {fs:.2f} Tf {layout['spacing']:.2f} Tc {(width - layout['main']) / 2:.2f} {baseline:.2f} Td "
               ).encode() + _pdf_text(text) + b" Tj ET\n"
    if subtitle:
        stream += (f"BT /F2 {layout['small']:.2f} Tf 0 Tc {(width - layout['sub']) / 2:.2f} {fs * 0.72:.2f} Td "
                   ).encode() + _pdf_text(subtitle) + b" Tj ET\n"
    return stream + b"Q\n", width, height


def _font_object(doc: pymupdf.Document, base_font: str) -> int:
    xref = doc.get_new_xref()
    doc.update_object(xref, f"<</Type/Font/Subtype/Type1/BaseFont/{base_font}/Encoding/WinAnsiEncoding>>")
    return xref


def add_stamp(
    page: pymupdf.Page,
    rect: pymupdf.Rect,
    text: str,
    color: Color = (0.094, 0.475, 0.306),
    subtitle: str = "",
    kind: str = "",
) -> pymupdf.Annot:
    """Sello de goma («APROBADO», «BORRADOR»...) con fecha o nombre opcional debajo.

    Se dibuja con su propia apariencia (así sale en el idioma de quien lo pone) y se
    ajusta, sin deformarse, centrado en ``rect`` (coordenadas visibles). ``kind`` es
    el tipo de sello («approved», «draft»...) y queda en el nombre de la anotación.
    """
    text = " ".join(text.split()).upper()
    subtitle = " ".join(subtitle.split())
    if not text:
        raise InvalidOptionError("sello sin texto", option="text")
    stream, width, height = _stamp_appearance(text, subtitle, color)
    area = pymupdf.Rect(rect).normalize()
    if area.width < 1 or area.height < 1:
        raise InvalidOptionError("sello demasiado pequeño", option="size")
    scale = min(area.width / width, area.height / height)
    w, h = width * scale, height * scale
    cx, cy = (area.x0 + area.x1) / 2, (area.y0 + area.y1) / 2
    visual = pymupdf.Rect(cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    annot = page.add_stamp_annot(_rect_to_page(page, visual), stamp=0)
    doc = page.parent
    # Con un nombre propio MuPDF nunca redibuja el sello con los suyos (en inglés); con
    # «Approved», «Draft»... lo haría al cambiar el comentario o el color.
    doc.xref_set_key(annot.xref, "Name", "/BlackPDF." + (re.sub(r"[^A-Za-z0-9]", "", kind) or "Custom"))
    annot.set_rect(_rect_to_page(page, visual))  # MuPDF lo había ajustado a la proporción de su sello
    annot.set_colors(stroke=color)
    annot.set_info(content=text + (f"\n{subtitle}" if subtitle else ""))
    _finish(annot)
    # Después de update(): si no, MuPDF vuelve a dibujar su sello en inglés encima.
    annot._setAP(stream)
    ap = int(doc.xref_get_key(annot.xref, "AP/N")[1].split()[0])
    # La caja del dibujo va en el sentido en que se ve la página: la matriz la endereza.
    doc.xref_set_key(ap, "BBox", f"[0 0 {width:.2f} {height:.2f}]")
    angle = math.radians(page.rotation)
    cos, sin = round(math.cos(angle), 6), round(math.sin(angle), 6)
    doc.xref_set_key(ap, "Matrix", f"[{cos} {sin} {-sin} {cos} 0 0]")
    doc.xref_set_key(ap, "Resources", f"<</Font<</F1 {_font_object(doc, 'Helvetica-Bold')} 0 R"
                                      f"/F2 {_font_object(doc, 'Helvetica')} 0 R>>>>")
    return annot


@dataclass(frozen=True)
class AnnotationInfo:
    xref: int
    kind: str
    rect: pymupdf.Rect
    """Rectángulo visible."""
    content: str


def list_annotations(page: pymupdf.Page) -> list[AnnotationInfo]:
    result = []
    for annot in page.annots() or []:
        result.append(
            AnnotationInfo(
                xref=annot.xref,
                kind=annot.type[1],
                rect=to_visual_rect(page, annot.rect),
                content=annot.info.get("content", "") if annot.info else "",
            )
        )
    return result


def _segment_distance(p: pymupdf.Point, a: pymupdf.Point, b: pymupdf.Point) -> float:
    dx, dy = b.x - a.x, b.y - a.y
    length = dx * dx + dy * dy
    if length == 0:
        return math.hypot(p.x - a.x, p.y - a.y)
    t = max(0.0, min(1.0, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length))
    return math.hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))


def _hits(annot: pymupdf.Annot, point: pymupdf.Point, tolerance: float) -> bool:
    rect = annot.rect + (-tolerance, -tolerance, tolerance, tolerance)
    if point not in rect:
        return False
    kind = annot.type[0]
    if kind in (pymupdf.PDF_ANNOT_INK, pymupdf.PDF_ANNOT_LINE, pymupdf.PDF_ANNOT_POLY_LINE):
        vertices = annot.vertices or []
        if vertices and isinstance(vertices[0], (list, tuple)) and vertices[0] and isinstance(vertices[0][0], (list, tuple)):
            strokes = vertices
        else:
            strokes = [vertices]
        reach = tolerance + (annot.border.get("width", 1) or 1)
        for stroke in strokes:
            pts = [pymupdf.Point(v) for v in stroke]
            if len(pts) == 1 and abs(pts[0] - point) <= reach:
                return True
            for a, b in zip(pts, pts[1:]):
                if _segment_distance(point, a, b) <= reach:
                    return True
        return False
    return True


def annotation_at(page: pymupdf.Page, point: PointLike, tolerance: float = 4.0) -> int | None:
    """xref de la anotación bajo `point` (visible), la más pequeña si hay varias."""
    target = _to_page(page, point)
    best: tuple[float, int] | None = None
    for annot in page.annots() or []:
        if _hits(annot, target, tolerance):
            area = max(annot.rect.width * annot.rect.height, 1.0)
            if best is None or area < best[0]:
                best = (area, annot.xref)
    return best[1] if best else None


def delete_annotation(page: pymupdf.Page, xref: int) -> bool:
    for annot in page.annots() or []:
        if annot.xref == xref:
            page.delete_annot(annot)
            return True
    return False


def set_note_text(page: pymupdf.Page, xref: int, text: str) -> bool:
    for annot in page.annots() or []:
        if annot.xref == xref:
            annot.set_info(content=text)
            annot.update()
            return True
    return False


def redact_areas(
    page: pymupdf.Page,
    rects: Sequence[pymupdf.Rect],
    fill: Color = (0.0, 0.0, 0.0),
) -> int:
    """Censura definitiva: elimina texto, imágenes y dibujos bajo cada zona.

    No es una anotación: el contenido desaparece del archivo y en su lugar
    queda un recuadro del color indicado. Devuelve cuántas zonas se aplicaron.
    """
    areas = [pymupdf.Rect(r).normalize() for r in rects]
    areas = [r for r in areas if r.width >= 1 and r.height >= 1]
    if not areas:
        raise InvalidOptionError("zona vacía", option="rects")
    for area in areas:
        page.add_redact_annot(_rect_to_page(page, area), fill=fill, cross_out=False)
    page.apply_redactions(
        images=pymupdf.PDF_REDACT_IMAGE_PIXELS,
        graphics=pymupdf.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED,
        text=pymupdf.PDF_REDACT_TEXT_REMOVE,
    )
    return len(areas)


def redact_text(doc: pymupdf.Document, query: str, fill: Color = (0.0, 0.0, 0.0)) -> int:
    """Censura todas las apariciones de `query` en el documento. Devuelve cuántas."""
    query = query.strip()
    if not query:
        raise InvalidOptionError("texto vacío", option="query")
    total = 0
    for page in doc:
        hits = page.search_for(query)
        if hits:
            redact_areas(page, [pymupdf.Rect(h) * page.rotation_matrix for h in hits], fill)
            total += len(hits)
    return total
