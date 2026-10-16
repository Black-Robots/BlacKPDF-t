"""Índice (marcadores), búsqueda de texto y enlaces, en coordenadas visibles."""

from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf


@dataclass
class OutlineItem:
    title: str
    page: int
    """Índice de página base 0, o -1 si el marcador no apunta a una página."""
    level: int
    children: list["OutlineItem"] = field(default_factory=list)


def get_outline(doc: pymupdf.Document) -> list[OutlineItem]:
    """Árbol de marcadores del documento."""
    roots: list[OutlineItem] = []
    stack: list[OutlineItem] = []
    for level, title, page, *_ in doc.get_toc(simple=True):
        item = OutlineItem(title=title.strip() or "-", page=page - 1 if page >= 1 else -1, level=level)
        while stack and stack[-1].level >= level:
            stack.pop()
        if stack:
            stack[-1].children.append(item)
        else:
            roots.append(item)
        stack.append(item)
    return roots


def search_page(page: pymupdf.Page, query: str) -> list[pymupdf.Rect]:
    """Rectángulos visibles de cada aparición de `query` (sin distinguir mayúsculas)."""
    query = query.strip()
    if not query:
        return []
    matrix = page.rotation_matrix
    return [pymupdf.Rect(rect) * matrix for rect in page.search_for(query)]


@dataclass(frozen=True)
class PageLink:
    rect: pymupdf.Rect
    """Rectángulo visible."""
    page: int
    """Destino interno (base 0) o -1."""
    uri: str
    """Destino externo o cadena vacía."""


def page_links(page: pymupdf.Page) -> list[PageLink]:
    matrix = page.rotation_matrix
    links = []
    for link in page.get_links():
        kind = link.get("kind")
        rect = pymupdf.Rect(link.get("from", pymupdf.Rect())) * matrix
        if kind == pymupdf.LINK_GOTO and link.get("page", -1) >= 0:
            links.append(PageLink(rect, int(link["page"]), ""))
        elif kind == pymupdf.LINK_URI and link.get("uri"):
            links.append(PageLink(rect, -1, str(link["uri"])))
        elif kind == pymupdf.LINK_NAMED and link.get("page", -1) is not None and link.get("page", -1) >= 0:
            links.append(PageLink(rect, int(link["page"]), ""))
    return links


def page_text(page: pymupdf.Page, clip: pymupdf.Rect | None = None) -> str:
    """Texto de la página o de una zona visible."""
    if clip is not None:
        clip = pymupdf.Rect(clip) * page.derotation_matrix
    return page.get_text("text", clip=clip, sort=True).strip()
