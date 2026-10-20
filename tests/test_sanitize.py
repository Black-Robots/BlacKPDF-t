"""Limpiar datos ocultos: quita lo elegido y deja lo demás."""

from __future__ import annotations

import pymupdf

from blackpdf.core.sanitize import clean_pdf, inspect


def _dirty(path):
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Contenido visible", fontsize=14)
    doc.set_metadata({"title": "Informe", "author": "Diego", "creator": "Programa interno v3"})
    doc.set_xml_metadata('<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
                         '<rdf:Description rdf:about=""/></rdf:RDF></x:xmpmeta>')
    doc.embfile_add("datos.xlsx", b"hoja", filename="datos.xlsx")
    page.add_text_annot((300, 300), "Comentario interno")
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(72, 60, 200, 80), "uri": "https://example.org"})
    # JavaScript al abrir el documento.
    js = doc.get_new_xref()
    doc.update_object(js, "<< /S /JavaScript /JS (app.alert('hola');) >>")
    doc.xref_set_key(doc.pdf_catalog(), "OpenAction", f"{js} 0 R")
    doc.save(path)
    return path


def test_inspect_finds_hidden_data(tmp_path):
    found = inspect(pymupdf.open(_dirty(tmp_path / "sucio.pdf")))
    assert {"title", "author", "creator"} <= set(found.metadata) and found.xmp
    assert found.javascript >= 1 and found.attachments == 1 and found.comments == 1 and found.links == 1


def test_default_clean_keeps_what_is_visible(tmp_path):
    source = _dirty(tmp_path / "sucio.pdf")
    result = clean_pdf(str(source), str(tmp_path / "limpio.pdf"))
    after = result.after
    assert not after.metadata and not after.xmp and after.javascript == 0 and after.attachments == 0
    assert after.comments == 1 and after.links == 1, "por defecto no se tocan comentarios ni enlaces"
    clean = pymupdf.open(result.path)
    assert "Contenido visible" in clean[0].get_text()
    assert b"Diego" not in result.path.read_bytes() and b"app.alert" not in result.path.read_bytes()
    assert pymupdf.open(source).metadata["author"] == "Diego", "el original no se toca"


def test_optional_cleaning(tmp_path):
    source = _dirty(tmp_path / "sucio.pdf")
    result = clean_pdf(str(source), str(tmp_path / "limpio.pdf"),
                       {"comments": True, "links": True, "metadata": False})
    assert result.after.comments == 0 and result.after.links == 0
    assert "author" in result.after.metadata, "lo que no se marca se conserva"
