"""Comentarios: notas, respuestas en hilo y comentarios sobre anotaciones.

Un comentario es cualquier anotación PDF con texto o que el usuario puede
comentar (resaltados, formas, firmas...). Las respuestas son notas con
/IRT apuntando a la anotación original, como hacen Acrobat y otros lectores.
Las coordenadas que se reciben y devuelven son las visibles de la página.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from .document import format_pdf_date
from .errors import InvalidOptionError

COMMENTABLE_SKIP = {
    pymupdf.PDF_ANNOT_POPUP,
    pymupdf.PDF_ANNOT_LINK,
    pymupdf.PDF_ANNOT_WIDGET,
    pymupdf.PDF_ANNOT_REDACT,
}
MARKUP_TYPES = {
    pymupdf.PDF_ANNOT_HIGHLIGHT,
    pymupdf.PDF_ANNOT_UNDERLINE,
    pymupdf.PDF_ANNOT_STRIKE_OUT,
    pymupdf.PDF_ANNOT_SQUIGGLY,
}
NOTE_SIZE = 20


@dataclass
class Comment:
    page: int
    xref: int
    kind: str
    """Tipo PDF de la anotación: "Text", "FreeText", "Highlight", "Ink"..."""
    content: str
    author: str
    modified: str
    rect: pymupdf.Rect
    """Rectángulo visible."""
    quote: str = ""
    """Texto marcado (resaltados, subrayados, tachados)."""
    color: tuple[float, float, float] | None = None
    replies: list["Comment"] = field(default_factory=list)


def _quote(page: pymupdf.Page, annot: pymupdf.Annot) -> str:
    if annot.type[0] not in MARKUP_TYPES:
        return ""
    vertices = annot.vertices or []
    parts: list[str] = []
    for start in range(0, len(vertices) - 3, 4):
        quad = pymupdf.Quad(vertices[start : start + 4])
        text = page.get_text("text", clip=quad.rect).strip()
        if text:
            parts.append(" ".join(text.split()))
    return " ".join(parts)


def _comment(page: pymupdf.Page, annot: pymupdf.Annot) -> Comment:
    info = annot.info or {}
    colors = annot.colors or {}
    stroke = colors.get("stroke") or colors.get("fill")
    return Comment(
        page=page.number,
        xref=annot.xref,
        kind=annot.type[1],
        content=(info.get("content") or "").strip(),
        author=(info.get("title") or "").strip(),
        modified=format_pdf_date(info.get("modDate") or info.get("creationDate") or ""),
        rect=pymupdf.Rect(annot.rect) * page.rotation_matrix,
        quote=_quote(page, annot),
        color=tuple(stroke[:3]) if stroke and len(stroke) >= 3 else None,
    )


def page_comments(page: pymupdf.Page) -> list[Comment]:
    """Comentarios de una página, con sus respuestas anidadas, de arriba abajo."""
    items: dict[int, Comment] = {}
    parents: dict[int, int] = {}
    for annot in page.annots() or []:
        if annot.type[0] in COMMENTABLE_SKIP:
            continue
        items[annot.xref] = _comment(page, annot)
        if annot.irt_xref:
            parents[annot.xref] = annot.irt_xref
    roots: list[Comment] = []
    for xref, comment in items.items():
        parent = parents.get(xref)
        if parent in items:
            items[parent].replies.append(comment)
        else:
            roots.append(comment)
    roots.sort(key=lambda c: (round(c.rect.y0), c.rect.x0))
    return roots


def list_comments(doc: pymupdf.Document) -> list[Comment]:
    """Todos los comentarios del documento, en orden de página y posición."""
    result: list[Comment] = []
    for page in doc:
        result.extend(page_comments(page))
    return result


def count_comments(doc: pymupdf.Document) -> int:
    total = 0
    for page in doc:
        for annot in page.annots() or []:
            if annot.type[0] not in COMMENTABLE_SKIP:
                total += 1
    return total


def _find(page: pymupdf.Page, xref: int) -> pymupdf.Annot:
    for annot in page.annots() or []:
        if annot.xref == xref:
            return annot
    raise InvalidOptionError(f"anotación {xref} no encontrada", option="xref")


def _stamp(annot: pymupdf.Annot, author: str | None = None, content: str | None = None) -> None:
    now = pymupdf.get_pdf_now()
    info = {"modDate": now}
    if not (annot.info or {}).get("creationDate"):
        info["creationDate"] = now
    if author is not None:
        info["title"] = author
    if content is not None:
        info["content"] = content
    annot.set_info(**info)
    annot.update()


def set_comment(page: pymupdf.Page, xref: int, text: str, author: str | None = None) -> None:
    """Cambia el texto del comentario de una anotación (vacío = sin comentario)."""
    annot = _find(page, xref)
    _stamp(annot, author=author, content=text.strip())


def add_page_comment(
    page: pymupdf.Page,
    text: str,
    author: str,
    point: tuple[float, float] | None = None,
    color: tuple[float, float, float] = (1.0, 0.78, 0.2),
) -> int:
    """Nota de comentario en la página. Sin punto, en la esquina superior derecha."""
    if not text.strip():
        raise InvalidOptionError("comentario vacío", option="text")
    if point is None:
        taken = [c.rect for c in page_comments(page) if c.kind == "Text"]
        x = page.rect.width - NOTE_SIZE - 16
        y = 16.0
        while any(abs(r.x0 - x) < NOTE_SIZE and abs(r.y0 - y) < NOTE_SIZE for r in taken):
            y += NOTE_SIZE + 8
        point = (x, y)
    target = pymupdf.Point(point) * page.derotation_matrix
    annot = page.add_text_annot(target, text.strip(), icon="Comment")
    annot.set_colors(stroke=color)
    _stamp(annot, author=author)
    return annot.xref


def add_reply(page: pymupdf.Page, parent_xref: int, text: str, author: str) -> int:
    """Respuesta a un comentario existente (anotación de texto con /IRT)."""
    if not text.strip():
        raise InvalidOptionError("respuesta vacía", option="text")
    parent = _find(page, parent_xref)
    corner = pymupdf.Point(parent.rect.x0, parent.rect.y0)
    reply = page.add_text_annot(corner, text.strip(), icon="Comment")
    reply.set_irt_xref(parent.xref)
    colors = parent.colors or {}
    if colors.get("stroke"):
        reply.set_colors(stroke=colors["stroke"])
    _stamp(reply, author=author)
    return reply.xref


def delete_comment(page: pymupdf.Page, xref: int) -> int:
    """Elimina la anotación y sus respuestas. Devuelve cuántas se borraron."""
    doomed = {xref}
    changed = True
    annots = list(page.annots() or [])
    while changed:
        changed = False
        for annot in annots:
            if annot.irt_xref in doomed and annot.xref not in doomed:
                doomed.add(annot.xref)
                changed = True
    removed = 0
    for target in sorted(doomed, reverse=True):
        # Se vuelve a buscar cada vez: borrar una anotación invalida las demás referencias.
        annot = next((a for a in page.annots() or [] if a.xref == target), None)
        if annot is not None:
            page.delete_annot(annot)
            removed += 1
    if removed == 0:
        raise InvalidOptionError(f"anotación {xref} no encontrada", option="xref")
    return removed
