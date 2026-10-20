"""Propiedades editables del documento: título, autor, asunto y palabras clave.

Se cambian en el diccionario Info y, si el PDF trae metadatos XMP (gestores
documentales, PDF/A), también ahí, sin tocar el resto del XMP.
"""

from __future__ import annotations

import re
from xml.etree import ElementTree

import pymupdf

FIELDS = ("title", "author", "subject", "keywords")

_NS = {
    "x": "adobe:ns:meta/",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "pdf": "http://ns.adobe.com/pdf/1.3/",
    "xmp": "http://ns.adobe.com/xap/1.0/",
}
_XML = "http://www.w3.org/XML/1998/namespace"


def read_metadata(doc: pymupdf.Document) -> dict[str, str]:
    meta = doc.metadata or {}
    return {field: (meta.get(field) or "").strip() for field in FIELDS}


def set_metadata(doc: pymupdf.Document, values: dict[str, str]) -> None:
    """Cambia los campos indicados (los demás se conservan) y la fecha de modificación."""
    meta = {k: v for k, v in (doc.metadata or {}).items() if k not in ("format", "encryption")}
    for field in FIELDS:
        if field in values:
            meta[field] = (values[field] or "").strip()
    meta["modDate"] = pymupdf.get_pdf_now()
    doc.set_metadata({k: v or "" for k, v in meta.items()})
    xml = doc.get_xml_metadata()
    if xml.strip():
        updated = update_xmp(xml, {field: values[field] for field in FIELDS if field in values})
        if updated is not None:
            doc.set_xml_metadata(updated)


def _q(prefix: str, name: str) -> str:
    return f"{{{_NS[prefix]}}}{name}"


def _descriptions(root) -> list:
    if root.tag == _q("rdf", "Description"):
        return [root]
    return list(root.iter(_q("rdf", "Description")))


def _holder(descriptions: list, prefix: str, name: str):
    """El bloque rdf:Description que ya tiene la propiedad (el XMP suele repartirlas); si no, el primero."""
    tag = _q(prefix, name)
    for description in descriptions:
        if tag in description.attrib or description.find(tag) is not None:
            return description
    return descriptions[0]


def _set_container(description, prefix: str, name: str, container: str, items: list[str]) -> None:
    """dc:title y dc:description van en rdf:Alt; dc:creator en rdf:Seq."""
    tag = _q(prefix, name)
    description.attrib.pop(tag, None)
    element = description.find(tag)
    if not items:
        if element is not None:
            description.remove(element)
        return
    if element is None:
        element = ElementTree.SubElement(description, tag)
    for child in list(element):
        element.remove(child)
    holder = ElementTree.SubElement(element, _q("rdf", container))
    for text in items:
        li = ElementTree.SubElement(holder, _q("rdf", "li"))
        if container == "Alt":
            li.set(f"{{{_XML}}}lang", "x-default")
        li.text = text


def _set_simple(description, prefix: str, name: str, value: str) -> None:
    tag = _q(prefix, name)
    element = description.find(tag)
    if tag in description.attrib or (element is None and value):
        description.attrib.pop(tag, None)
        if value:
            description.set(tag, value)
        return
    if element is not None:
        if value:
            element.text = value
        else:
            description.remove(element)


def update_xmp(xml: str, values: dict[str, str]) -> str | None:
    """XMP con los campos nuevos; None si no se entiende (entonces se deja como estaba)."""
    for prefix, uri in _NS.items():
        ElementTree.register_namespace(prefix, uri)
    # Los prefijos propios del archivo (pdfaid, xmpMM...) se conservan tal cual.
    for prefix, uri in re.findall(r'xmlns:([A-Za-z_][\w.-]*)="([^"]+)"', xml):
        try:
            ElementTree.register_namespace(prefix, uri)
        except ValueError:
            pass
    header = re.match(r"\s*(<\?xpacket[^>]*\?>)", xml)
    trailer = re.search(r"(<\?xpacket end=[^>]*\?>)\s*$", xml)
    body = xml[header.end() if header else 0:trailer.start() if trailer else len(xml)]
    try:
        root = ElementTree.fromstring(body.strip())
    except ElementTree.ParseError:
        return None
    descriptions = _descriptions(root)
    if not descriptions:
        return None
    if "title" in values:
        text = values["title"].strip()
        _set_container(_holder(descriptions, "dc", "title"), "dc", "title", "Alt", [text] if text else [])
    if "subject" in values:
        text = values["subject"].strip()
        _set_container(_holder(descriptions, "dc", "description"), "dc", "description", "Alt", [text] if text else [])
    if "author" in values:
        authors = [a.strip() for a in re.split(r"[;\n]", values["author"]) if a.strip()]
        _set_container(_holder(descriptions, "dc", "creator"), "dc", "creator", "Seq", authors)
    if "keywords" in values:
        _set_simple(_holder(descriptions, "pdf", "Keywords"), "pdf", "Keywords", values["keywords"].strip())
    _set_simple(_holder(descriptions, "xmp", "ModifyDate"), "xmp", "ModifyDate", _xmp_now())
    text = ElementTree.tostring(root, encoding="unicode")
    return (header.group(1) + "\n" if header else "") + text + ("\n" + trailer.group(1) if trailer else "")


def _xmp_now() -> str:
    import datetime as dt

    return dt.datetime.now().astimezone().isoformat(timespec="seconds")
