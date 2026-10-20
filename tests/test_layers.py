"""Capas (contenido opcional)."""

from __future__ import annotations

import pymupdf

from blackpdf.core import layers
from blackpdf.core.document import restore


def _plan() -> tuple[pymupdf.Document, dict[str, int]]:
    """Plano con una capa bloqueada, un grupo con título y un grupo de opción única."""
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=100)
    xrefs = {name: doc.add_ocg(name, on=on) for name, on in
             (("Planta", True), ("Agua", False), ("Luz", True), ("Opción A", True), ("Opción B", False))}
    for i, xref in enumerate(xrefs.values()):
        page.draw_rect(pymupdf.Rect(10 + i * 35, 10, 35 + i * 35, 35), color=(1, 0, 0), fill=(1, 0, 0), oc=xref)
    catalog = doc.pdf_catalog()
    x = xrefs
    doc.xref_set_key(catalog, "OCProperties/D/Order",
                     f"[{x['Planta']} 0 R [(Instalaciones) {x['Agua']} 0 R {x['Luz']} 0 R] "
                     f"{x['Opción A']} 0 R {x['Opción B']} 0 R]")
    doc.xref_set_key(catalog, "OCProperties/D/RBGroups", f"[[{x['Opción A']} 0 R {x['Opción B']} 0 R]]")
    doc.xref_set_key(catalog, "OCProperties/D/Locked", f"[{x['Planta']} 0 R]")
    return pymupdf.open(stream=doc.tobytes(), filetype="pdf"), xrefs


def _red(doc: pymupdf.Document, index: int) -> bool:
    return doc[0].get_pixmap().pixel(20 + index * 35, 20) == (255, 0, 0)


def test_list_layers_with_groups_locks_and_radio_buttons():
    doc, _ = _plan()
    found = layers.list_layers(doc)
    assert [(item.name, item.depth, item.kind, item.visible) for item in found] == [
        ("Planta", 0, "checkbox", True), ("Instalaciones", 0, "label", False), ("Agua", 1, "checkbox", False),
        ("Luz", 1, "checkbox", True), ("Opción A", 0, "radiobox", True), ("Opción B", 0, "radiobox", False)]
    assert not found[0].toggleable and not found[1].toggleable and found[2].toggleable
    assert not layers.has_layers(pymupdf.open()) and layers.list_layers(pymupdf.open()) == []


def test_toggling_changes_what_is_drawn_but_not_the_file():
    doc, _ = _plan()
    assert [_red(doc, i) for i in range(5)] == [True, False, True, True, False]
    layers.set_visible(doc, 2, True)        # Agua
    layers.set_visible(doc, 3, False)       # Luz
    layers.set_visible(doc, 5, True)        # Opción B: apaga la A
    layers.set_visible(doc, 0, False)       # Planta está bloqueada: no cambia
    assert [_red(doc, i) for i in range(5)] == [True, True, False, False, True]
    assert not layers.is_default(doc)
    reopened = restore(doc.tobytes())
    assert layers.is_default(reopened) and [_red(reopened, i) for i in range(5)] == [True, False, True, True, False]


def test_state_survives_reopening_and_can_be_saved_as_default():
    doc, _ = _plan()
    layers.set_visible(doc, 2, True)
    layers.set_visible(doc, 5, True)
    state = layers.current_state(doc)
    again = restore(doc.tobytes())
    layers.restore_state(again, state)
    assert layers.current_state(again) == state
    layers.save_as_default(again)
    assert layers.is_default(again)
    saved = restore(again.tobytes())
    assert layers.current_state(saved) == state, "el PDF se abre ya con esas capas"
    assert [_red(saved, i) for i in range(5)] == [True, True, True, False, True]


def test_show_and_hide_all():
    doc, _ = _plan()
    layers.set_all_visible(doc, False)
    visible = {item.name: item.visible for item in layers.list_layers(doc)}
    assert visible["Planta"] and not visible["Agua"] and not visible["Luz"], "las bloqueadas se quedan como están"
    assert visible["Opción A"], "los grupos de opción única no se tocan"
    layers.set_all_visible(doc, True)
    assert {item.name: item.visible for item in layers.list_layers(doc)}["Agua"]
