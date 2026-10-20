"""Propiedades editables: diccionario Info y XMP coherentes."""

from __future__ import annotations

import pymupdf

from blackpdf.core import metadata

XMP = """<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:dc="http://purl.org/dc/elements/1.1/">
   <dc:title><rdf:Alt><rdf:li xml:lang="x-default">Título viejo</rdf:li></rdf:Alt></dc:title>
   <dc:creator><rdf:Seq><rdf:li>Alguien</rdf:li></rdf:Seq></dc:creator>
  </rdf:Description>
  <rdf:Description rdf:about="" xmlns:pdf="http://ns.adobe.com/pdf/1.3/" pdf:Keywords="viejo"/>
  <rdf:Description rdf:about="" xmlns:pdfaid="http://www.aiim.org/pdfa/ns/id/" pdfaid:part="2" pdfaid:conformance="B"/>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>"""


def _doc() -> pymupdf.Document:
    doc = pymupdf.open()
    doc.new_page()
    doc.set_metadata({"title": "Título viejo", "author": "Alguien", "creator": "Programa X",
                      "creationDate": "D:20200101000000"})
    return doc


def test_info_dictionary_keeps_other_fields():
    doc = _doc()
    metadata.set_metadata(doc, {"title": "Planos de la nave", "keywords": "nave, estructura"})
    doc = pymupdf.open("pdf", doc.tobytes())
    values = metadata.read_metadata(doc)
    assert values == {"title": "Planos de la nave", "author": "Alguien", "subject": "", "keywords": "nave, estructura"}
    assert doc.metadata["creator"] == "Programa X" and doc.metadata["creationDate"] == "D:20200101000000"
    assert doc.metadata["modDate"].startswith("D:2")


def test_xmp_is_updated_in_place_and_keeps_pdfa():
    doc = _doc()
    doc.set_xml_metadata(XMP)
    metadata.set_metadata(doc, {"title": "Planos de la nave", "author": "Ana; Luis", "keywords": "nave"})
    xml = pymupdf.open("pdf", doc.tobytes()).get_xml_metadata()
    assert "Planos de la nave" in xml and "Título viejo" not in xml
    assert xml.count("Planos de la nave") == 1, "sin títulos duplicados"
    assert "<rdf:li>Ana</rdf:li>" in xml and "<rdf:li>Luis</rdf:li>" in xml and "Alguien" not in xml
    assert 'pdf:Keywords="nave"' in xml and "viejo" not in xml.replace("Título viejo", "")
    assert 'pdfaid:part="2"' in xml, "la marca de PDF/A se conserva"
    assert xml.startswith("<?xpacket begin") and xml.rstrip().endswith('<?xpacket end="w"?>')


def test_broken_xmp_is_left_alone():
    assert metadata.update_xmp("<x:xmpmeta esto no es xml", {"title": "A"}) is None
