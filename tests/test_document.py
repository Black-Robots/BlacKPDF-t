import os
import stat

import pymupdf
import pytest

from blackpdf.core.document import (
    document_info,
    format_pdf_date,
    needs_password,
    open_pdf,
    restore,
    save_pdf,
    snapshot,
    source_size,
    source_stem,
)
from blackpdf.core.errors import FileOpenError, NotPdfError, PasswordRequiredError, WrongPasswordError
from blackpdf.core.security import protect_pdf

from .conftest import build_pdf, page_labels


def test_open_from_path_bytes_and_document(make_pdf):
    path = make_pdf(pages=2)
    from_path = open_pdf(path)
    from_bytes = open_pdf(path.read_bytes())
    from_doc = open_pdf(from_path)
    assert from_path.page_count == from_bytes.page_count == from_doc.page_count == 2
    from_doc.delete_page(0)
    assert from_path.page_count == 2, "la copia no debe tocar el original"


def test_open_does_not_lock_file(make_pdf, tmp_path):
    path = make_pdf(pages=1)
    doc = open_pdf(path)
    save_pdf(doc, path)
    assert page_labels(path) == ["P1"]


def test_open_missing_file(tmp_path):
    with pytest.raises(FileOpenError):
        open_pdf(tmp_path / "no-existe.pdf")


def test_open_garbage(tmp_path):
    bad = tmp_path / "malo.pdf"
    bad.write_bytes(b"esto no es un pdf")
    with pytest.raises(NotPdfError):
        open_pdf(bad)


def test_open_image_converts_to_pdf(make_image):
    doc = open_pdf(make_image(fmt="png"))
    assert doc.is_pdf and doc.page_count == 1


def test_password_flow(make_pdf, tmp_path):
    locked = tmp_path / "cifrado.pdf"
    protect_pdf(make_pdf(pages=1), locked, "secreto")
    assert needs_password(locked)
    with pytest.raises(PasswordRequiredError):
        open_pdf(locked)
    with pytest.raises(WrongPasswordError):
        open_pdf(locked, "otra")
    assert open_pdf(locked, "secreto").page_count == 1


@pytest.mark.skipif(os.name == "nt", reason="permisos POSIX")
def test_save_is_atomic_and_keeps_permissions(make_pdf):
    path = make_pdf(pages=2)
    os.chmod(path, 0o640)
    doc = open_pdf(path)
    doc.delete_page(0)
    save_pdf(doc, path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert page_labels(path) == ["P2"]
    leftovers = [p for p in path.parent.iterdir() if p.name.startswith(".blackpdf-")]
    assert not leftovers


def test_snapshot_roundtrip():
    doc = build_pdf(3)
    data = snapshot(doc)
    copy = restore(data)
    assert page_labels(copy) == ["P1", "P2", "P3"]


def test_source_helpers(make_pdf):
    path = make_pdf(pages=1, name="informe anual.pdf")
    assert source_stem(path) == "informe anual"
    assert source_stem(b"x") == "documento"
    assert source_size(path) == path.stat().st_size


def test_document_info():
    doc = build_pdf(2)
    doc.set_metadata({"title": "Prueba", "author": "Ana", "creationDate": "D:20240131120500+01'00'"})
    info = document_info(doc)
    assert info.page_count == 2
    assert info.title == "Prueba" and info.author == "Ana"
    assert info.created == "31/01/2024 12:05"
    assert round(info.page_size[0]) == 595


def test_format_pdf_date():
    assert format_pdf_date("D:20231105") == "05/11/2023"
    assert format_pdf_date("") == ""
    assert format_pdf_date("basura") == ""
