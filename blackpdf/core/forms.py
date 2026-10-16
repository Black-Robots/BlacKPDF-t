"""Rellenar formularios PDF (AcroForm) y aplanar documentos."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from .document import PdfSource, open_pdf, save_pdf
from .errors import InvalidOptionError
from .progress import ProgressCallback, report

FIELD_KINDS = {
    pymupdf.PDF_WIDGET_TYPE_TEXT: "text",
    pymupdf.PDF_WIDGET_TYPE_CHECKBOX: "checkbox",
    pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: "radio",
    pymupdf.PDF_WIDGET_TYPE_COMBOBOX: "combobox",
    pymupdf.PDF_WIDGET_TYPE_LISTBOX: "listbox",
    pymupdf.PDF_WIDGET_TYPE_SIGNATURE: "signature",
    pymupdf.PDF_WIDGET_TYPE_BUTTON: "button",
}
EDITABLE_KINDS = ("text", "checkbox", "radio", "combobox", "listbox")


@dataclass(frozen=True)
class FieldKey:
    page: int
    xref: int


@dataclass
class FormField:
    key: FieldKey
    name: str
    label: str
    kind: str
    value: str | bool
    options: list[str] = field(default_factory=list)
    read_only: bool = False
    multiline: bool = False
    max_length: int = 0
    on_state: str = ""


def _is_checked(widget: pymupdf.Widget) -> bool:
    value = widget.field_value
    if isinstance(value, bool):
        return value
    text = str(value or "")
    return bool(text) and text not in ("Off", "false", "0")


def _choices(widget: pymupdf.Widget) -> list[str]:
    values = widget.choice_values or []
    result = []
    for item in values:
        if isinstance(item, (list, tuple)):
            result.append(str(item[-1]))
        else:
            result.append(str(item))
    return result


def list_fields(doc: pymupdf.Document) -> list[FormField]:
    """Campos de formulario en orden de página."""
    fields: list[FormField] = []
    for page in doc:
        for widget in page.widgets() or []:
            kind = FIELD_KINDS.get(widget.field_type, "")
            if not kind:
                continue
            if kind in ("checkbox", "radio"):
                value: str | bool = _is_checked(widget)
            else:
                value = "" if widget.field_value is None else str(widget.field_value)
            flags = widget.field_flags or 0
            try:
                on_state = widget.on_state() if kind in ("checkbox", "radio") else ""
            except Exception:
                on_state = ""
            fields.append(
                FormField(
                    key=FieldKey(page.number, widget.xref),
                    name=widget.field_name or "",
                    label=widget.field_label or widget.field_name or "",
                    kind=kind,
                    value=value,
                    options=_choices(widget) if kind in ("combobox", "listbox") else [],
                    read_only=bool(flags & pymupdf.PDF_FIELD_IS_READ_ONLY),
                    multiline=bool(flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE) if kind == "text" else False,
                    max_length=int(getattr(widget, "text_maxlen", 0) or 0),
                    on_state=str(on_state or ""),
                )
            )
    return fields


def fill_fields(doc: pymupdf.Document, values: Mapping[FieldKey, str | bool]) -> int:
    """Asigna valores a los campos indicados. Devuelve cuántos se cambiaron."""
    changed = 0
    by_page: dict[int, dict[int, str | bool]] = {}
    for key, value in values.items():
        by_page.setdefault(key.page, {})[key.xref] = value
    for page_number, entries in by_page.items():
        if page_number < 0 or page_number >= doc.page_count:
            raise InvalidOptionError(f"página {page_number}", option="page")
        page = doc[page_number]
        for widget in page.widgets() or []:
            if widget.xref not in entries:
                continue
            value = entries[widget.xref]
            kind = FIELD_KINDS.get(widget.field_type, "")
            if kind in ("checkbox", "radio"):
                checked = bool(value)
                if checked == _is_checked(widget):
                    continue
                if kind == "radio":
                    widget.field_value = checked
                else:
                    widget.field_value = widget.on_state() if checked else "Off"
            elif kind in ("text", "combobox", "listbox"):
                text = "" if value is None else str(value)
                if text == ("" if widget.field_value is None else str(widget.field_value)):
                    continue
                widget.field_value = text
            else:
                continue
            widget.update()
            changed += 1
    return changed


def fill_form_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    values: Mapping[FieldKey, str | bool],
    *,
    flatten: bool = False,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    doc = open_pdf(source, password)
    try:
        report(progress, 0, 2)
        fill_fields(doc, values)
        if flatten:
            flatten_document(doc)
        report(progress, 1, 2)
        path = save_pdf(doc, output, garbage=3)
        report(progress, 2, 2)
        return path
    finally:
        doc.close()


def flatten_document(doc: pymupdf.Document, *, annotations: bool = True, forms: bool = True) -> None:
    """Convierte anotaciones y campos en contenido fijo de la página."""
    if not annotations and not forms:
        raise InvalidOptionError("nada que aplanar", option="flatten")
    doc.bake(annots=annotations, widgets=forms)


def flatten_pdf(
    source: PdfSource,
    output: str | os.PathLike,
    *,
    annotations: bool = True,
    forms: bool = True,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    doc = open_pdf(source, password)
    try:
        report(progress, 0, 2)
        flatten_document(doc, annotations=annotations, forms=forms)
        report(progress, 1, 2)
        path = save_pdf(doc, output, garbage=4)
        report(progress, 2, 2)
        return path
    finally:
        doc.close()
