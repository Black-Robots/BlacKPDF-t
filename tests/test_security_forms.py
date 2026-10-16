"""Proteger, desbloquear, formularios y aplanar."""

import pymupdf
import pytest

from blackpdf.core.errors import InvalidOptionError, PasswordRequiredError, WrongPasswordError
from blackpdf.core.forms import (
    FieldKey,
    fill_fields,
    fill_form_pdf,
    flatten_pdf,
    list_fields,
)
from blackpdf.core.security import Permissions, is_encrypted, protect_pdf, unlock_pdf


def test_protect_uses_aes256(make_pdf, tmp_path):
    output = tmp_path / "protegido.pdf"
    protect_pdf(make_pdf(), output, "clave")
    doc = pymupdf.open(output)
    assert doc.needs_pass
    assert doc.authenticate("clave")
    assert "AES" in doc.metadata["encryption"] and "256" in doc.metadata["encryption"]
    assert is_encrypted(output)


def test_protect_with_restrictions(make_pdf, tmp_path):
    output = tmp_path / "restringido.pdf"
    protect_pdf(make_pdf(), output, "abrir", permissions=Permissions(print=False, copy=False))
    doc = pymupdf.open(output)
    assert doc.authenticate("abrir")
    perms = Permissions.from_flags(doc.permissions)
    assert not perms.print and not perms.copy


def test_protect_requires_password(make_pdf, tmp_path):
    with pytest.raises(InvalidOptionError):
        protect_pdf(make_pdf(), tmp_path / "x.pdf", "")


def test_unlock(make_pdf, tmp_path):
    locked = tmp_path / "l.pdf"
    protect_pdf(make_pdf(pages=2), locked, "1234")
    with pytest.raises(PasswordRequiredError):
        unlock_pdf(locked, tmp_path / "u.pdf")
    with pytest.raises(WrongPasswordError):
        unlock_pdf(locked, tmp_path / "u.pdf", "0000")
    output = unlock_pdf(locked, tmp_path / "u.pdf", "1234")
    doc = pymupdf.open(output)
    assert not doc.needs_pass and not doc.is_encrypted
    assert doc.page_count == 2
    assert not is_encrypted(output)


def test_unlock_owner_only_restrictions(make_pdf, tmp_path):
    src = pymupdf.open(make_pdf())
    locked = tmp_path / "owner.pdf"
    src.save(locked, encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw="dueño", user_pw="", permissions=0)
    output = unlock_pdf(locked, tmp_path / "libre.pdf")
    assert not pymupdf.open(output).is_encrypted


def _form_pdf(path):
    doc = pymupdf.open()
    page = doc.new_page()
    specs = [
        ("nombre", pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.Rect(50, 50, 250, 70)),
        ("acepto", pymupdf.PDF_WIDGET_TYPE_CHECKBOX, pymupdf.Rect(50, 90, 66, 106)),
        ("pais", pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.Rect(50, 120, 250, 140)),
    ]
    for name, kind, rect in specs:
        widget = pymupdf.Widget()
        widget.field_name = name
        widget.field_type = kind
        widget.rect = rect
        if kind == pymupdf.PDF_WIDGET_TYPE_COMBOBOX:
            widget.choice_values = ["España", "México", "Argentina"]
            widget.field_value = "España"
        page.add_widget(widget)
    doc.save(path)
    doc.close()
    return path


def test_list_and_fill_fields(tmp_path):
    source = _form_pdf(tmp_path / "form.pdf")
    doc = pymupdf.open(source)
    fields = {f.name: f for f in list_fields(doc)}
    assert set(fields) == {"nombre", "acepto", "pais"}
    assert fields["acepto"].kind == "checkbox" and fields["acepto"].value is False
    assert fields["pais"].options == ["España", "México", "Argentina"]
    changed = fill_fields(
        doc,
        {fields["nombre"].key: "Lucía", fields["acepto"].key: True, fields["pais"].key: "México"},
    )
    assert changed == 3
    refreshed = {f.name: f for f in list_fields(doc)}
    assert refreshed["nombre"].value == "Lucía"
    assert refreshed["acepto"].value is True
    assert refreshed["pais"].value == "México"


def test_fill_form_and_flatten(tmp_path):
    source = _form_pdf(tmp_path / "form.pdf")
    doc = pymupdf.open(source)
    key = next(f.key for f in list_fields(doc) if f.name == "nombre")
    output = tmp_path / "relleno.pdf"
    fill_form_pdf(source, output, {key: "Texto fijo"}, flatten=True)
    result = pymupdf.open(output)
    assert list(result[0].widgets()) == []
    assert "Texto fijo" in result[0].get_text()


def test_flatten_annotations(make_pdf, tmp_path):
    source = make_pdf(pages=1)
    doc = pymupdf.open(source)
    doc[0].add_freetext_annot(pymupdf.Rect(50, 300, 300, 340), "Nota fija", fontsize=14)
    doc.saveIncr()
    doc.close()
    output = tmp_path / "plano.pdf"
    flatten_pdf(source, output)
    page = pymupdf.open(output)[0]
    assert len(list(page.annots())) == 0
    assert "Nota fija" in page.get_text()


def test_flatten_requires_something(make_pdf, tmp_path):
    with pytest.raises(InvalidOptionError):
        flatten_pdf(make_pdf(), tmp_path / "x.pdf", annotations=False, forms=False)


def test_fill_rejects_bad_page(tmp_path):
    doc = pymupdf.open(_form_pdf(tmp_path / "f.pdf"))
    with pytest.raises(InvalidOptionError):
        fill_fields(doc, {FieldKey(9, 1): "x"})
