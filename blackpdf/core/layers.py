"""Capas (contenido opcional, OCG): planos con cotas, mobiliario o instalaciones por separado.

Encender o apagar una capa solo cambia lo que se ve (y lo que se imprime o exporta desde
el visor); no modifica el archivo. Para que el PDF se abra con otra combinación de capas
hay que guardarla como vista inicial (`save_as_default`).
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf

LayerState = list[tuple[str, bool]]
"""Nombre y visibilidad de cada entrada de la lista de capas, en orden."""


@dataclass
class Layer:
    number: int
    """Posición en la lista de capas del documento (la que entiende MuPDF)."""
    name: str
    depth: int
    kind: str
    """"checkbox", "radiobox" (solo una del grupo a la vez) o "label" (título de un grupo)."""
    visible: bool
    locked: bool

    @property
    def toggleable(self) -> bool:
        return self.kind != "label" and not self.locked


def has_layers(doc: pymupdf.Document) -> bool:
    try:
        return doc.is_pdf and bool(doc.get_ocgs())
    except Exception:
        return False


def list_layers(doc: pymupdf.Document) -> list[Layer]:
    if not has_layers(doc):
        return []
    return [Layer(int(item["number"]), str(item.get("text") or ""), int(item.get("depth") or 0),
                  str(item.get("type") or "checkbox"), bool(item.get("on")), bool(item.get("locked")))
            for item in doc.layer_ui_configs()]


def set_visible(doc: pymupdf.Document, number: int, visible: bool) -> None:
    """Enciende o apaga una capa; en un grupo de opción única, encender una apaga las demás."""
    doc.set_layer_ui_config(number, 0 if visible else 2)


def set_all_visible(doc: pymupdf.Document, visible: bool) -> None:
    for layer in list_layers(doc):
        if layer.toggleable and layer.kind == "checkbox" and layer.visible != visible:
            set_visible(doc, layer.number, visible)


def current_state(doc: pymupdf.Document) -> LayerState:
    return [(layer.name, layer.visible) for layer in list_layers(doc)]


def restore_state(doc: pymupdf.Document, state: LayerState | None) -> None:
    """Vuelve a aplicar lo que se veía (al deshacer, el documento se reabre con su vista inicial)."""
    layers = list_layers(doc)
    if not state or [layer.name for layer in layers] != [name for name, _ in state]:
        return
    # Primero se apaga y luego se enciende: así los grupos de opción única quedan como estaban.
    for wanted in (False, True):
        for layer, (_name, visible) in zip(layers, state):
            if visible == wanted and layer.toggleable and layer.visible != visible:
                set_visible(doc, layer.number, visible)


def _visible_ocgs(doc: pymupdf.Document) -> tuple[list[int], list[int]]:
    ocgs = doc.get_ocgs()
    return sorted(x for x, item in ocgs.items() if item.get("on")), sorted(x for x, item in ocgs.items() if not item.get("on"))


def is_default(doc: pymupdf.Document) -> bool:
    """Si lo que se ve ahora es lo mismo que se ve al abrir el archivo."""
    if not has_layers(doc):
        return True
    config = doc.get_layer(-1)
    on, off = set(config.get("on") or []), set(config.get("off") or [])
    base = str(config.get("basestate") or "ON").upper() != "OFF"
    for xref, item in doc.get_ocgs().items():
        opened = True if xref in on else False if xref in off else base
        if bool(item.get("on")) != opened:
            return False
    return True


def save_as_default(doc: pymupdf.Document) -> None:
    """Guarda las capas que se ven ahora como las que se muestran al abrir el PDF."""
    on, off = _visible_ocgs(doc)
    doc.set_layer(-1, on=on, off=off)
