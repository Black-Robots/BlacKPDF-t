"""Archivos adjuntos: del documento y de anotaciones de página."""

from __future__ import annotations

import pymupdf
import pytest

from blackpdf.core import attachments as att
from blackpdf.core.errors import FileOpenError

STEP = b"ISO-10303-21;\nHEADER;\nENDSEC;\n" * 50


def make_doc() -> pymupdf.Document:
    doc = pymupdf.open()
    doc.new_page()
    page = doc.new_page()
    doc.embfile_add("planos.step", STEP, filename="planos.step", ufilename="planos.step", desc="Modelo 3D")
    page.add_file_annot((100, 120), b"medidas", "medidas.txt", desc="Cotas de la pieza")
    # Guardar y volver a abrir: así se lee igual que un PDF recibido.
    return pymupdf.open("pdf", doc.tobytes())


def test_lists_document_and_page_attachments():
    doc = make_doc()
    items = att.list_attachments(doc)
    assert [(a.kind, a.filename, a.page) for a in items] == [
        (att.KIND_DOCUMENT, "planos.step", None), (att.KIND_PAGE, "medidas.txt", 1)]
    assert items[0].size == len(STEP) and items[0].description == "Modelo 3D"
    assert items[1].description == "Cotas de la pieza" and items[1].rect is not None
    assert att.count_attachments(doc) == 2
    assert att.read_attachment(doc, items[0]) == STEP
    assert att.read_attachment(doc, items[1]) == b"medidas"


def test_save_all_never_overwrites_or_escapes_folder(tmp_path):
    doc = make_doc()
    doc.embfile_add("../../fuera.txt", b"x", filename="../../fuera.txt")
    folder = tmp_path / "adjuntos"
    folder.mkdir()
    (folder / "planos.step").write_bytes(b"ya estaba")
    paths = att.save_all(doc, att.list_attachments(doc), str(folder))
    names = sorted(p.split("/")[-1] for p in paths)
    assert names == ["fuera.txt", "medidas.txt", "planos (2).step"]
    assert (folder / "planos.step").read_bytes() == b"ya estaba"
    assert (folder / "planos (2).step").read_bytes() == STEP
    assert not (tmp_path / "fuera.txt").exists()


def test_safe_filename():
    assert att.safe_filename("../../etc/passwd") == "passwd"
    assert att.safe_filename("C:\\Windows\\a<b>.txt") == "a_b_.txt"
    assert att.safe_filename("CON.txt") == "_CON.txt"
    assert att.safe_filename("") == "adjunto"


def test_add_and_remove_attachments(tmp_path):
    doc = make_doc()
    source = tmp_path / "planos.step"
    source.write_bytes(b"otro")
    name = att.add_attachment(doc, str(source), "Revisión B")
    assert name == "planos (2).step", "no sustituye a un adjunto con el mismo nombre"
    added = [a for a in att.list_attachments(doc) if a.name == name][0]
    assert added.filename == "planos.step" and added.description == "Revisión B"
    assert att.read_attachment(doc, added) == b"otro"
    for item in att.list_attachments(doc):
        att.remove_attachment(doc, item)
    assert att.list_attachments(doc) == []
    with pytest.raises(FileOpenError):
        att.add_attachment(doc, str(tmp_path / "no-existe.step"))


def test_dangerous_types():
    assert att.is_dangerous("factura.pdf.exe") and att.is_dangerous("script.JS")
    assert att.is_dangerous("instalar.bat") and att.is_dangerous("acceso.lnk")
    assert not att.is_dangerous("planos.step") and not att.is_dangerous("datos.xlsx")
