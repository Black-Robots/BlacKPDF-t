"""Tablas del PDF a Excel (.xlsx) o CSV.

Las tablas se detectan con PyMuPDF (por sus líneas y por cómo se alinea el texto). El
.xlsx se escribe aquí mismo, sin dependencias: un libro con una hoja por tabla, la fila
de títulos en negrita y fija, y los números como números para poder sumarlos.
"""

from __future__ import annotations

import csv
import io
import os
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

import pymupdf

from .document import PdfSource, open_pdf
from .errors import InvalidOptionError
from .files import atomic_write
from .progress import ProgressCallback, report
from .ranges import parse_page_selection

FORMATS = ("xlsx", "csv")


@dataclass
class Table:
    page: int
    """Página (desde 0)."""
    index: int
    """Número de la tabla dentro de su página (desde 1)."""
    rows: list[list[str]]
    header: bool = False
    """La primera fila son los títulos de las columnas."""


@dataclass
class TablesResult:
    paths: list[Path] = field(default_factory=list)
    tables: int = 0


def _clean(value) -> str:
    text = "" if value is None else str(value)
    return "\n".join(line.strip() for line in text.replace("\r", "").split("\n")).strip()


def find_tables(
    source: PdfSource,
    pages: str = "",
    *,
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> list[Table]:
    pymupdf.no_recommend_layout()
    doc = open_pdf(source, password)
    try:
        indices = parse_page_selection(pages, doc.page_count, allow_empty_all=True)
        found: list[Table] = []
        for done, number in enumerate(indices):
            report(progress, done, len(indices))
            try:
                detected = doc[number].find_tables().tables
            except Exception:
                continue  # una página rara no impide sacar las tablas de las demás
            for position, item in enumerate(detected, 1):
                rows = [[_clean(cell) for cell in row] for row in item.extract()]
                header = False
                names = [_clean(name) for name in (item.header.names or [])]
                if item.header.external and any(names):
                    rows.insert(0, names)  # los títulos están justo encima de la tabla
                    header = True
                elif rows and names and [_clean(c) for c in rows[0]] == names and any(names):
                    header = True
                rows = [row for row in rows if any(row)]
                width = max((len(row) for row in rows), default=0)
                if len(rows) < 2 or width < 2:
                    continue  # una sola fila o columna: texto alineado, no una tabla
                found.append(Table(number, position, [row + [""] * (width - len(row)) for row in rows], header))
        report(progress, len(indices), len(indices))
        return found
    finally:
        doc.close()


# ---------- números ----------
_PLAIN = re.compile(r"^[-+]?\d+(?:[.,]\d+)?$")
_GROUPED = re.compile(r"^[-+]?\d{1,3}(?:([.,   ])\d{3})+(?:([.,])\d+)?$")


def parse_number(text: str, decimal: str = ",") -> int | float | None:
    """Número escrito como en el PDF («1.234,56», «1,234.56», «-12»), o None si no lo es.

    ``decimal`` resuelve los casos dudosos como «1.234» (mil doscientos o uno coma dos).
    """
    value = text.strip().replace("−", "-")
    if not value or len(value) > 24:
        return None
    grouped = _GROUPED.match(value)
    if grouped and grouped.group(1) != grouped.group(2):
        separator, fraction = grouped.group(1), grouped.group(2)
        ambiguous = fraction is None and separator in ".," and separator == decimal and value.count(separator) == 1
        if not ambiguous:
            digits = value.replace(separator, "")
            if fraction:
                digits = digits.replace(fraction, ".")
            return _to_number(digits)
    if _PLAIN.match(value):
        return _to_number(value.replace(",", "."))
    return None


def _number_style(text: str, value: int | float) -> int:
    """Estilo de celda que muestra el número como en el PDF: «420,00» con dos decimales, «1.200» con miles."""
    text = text.strip()
    grouped = _GROUPED.match(text)
    thousands = grouped is not None and not (isinstance(value, float) and grouped.group(2) is None)
    decimals = 0
    if isinstance(value, float):
        fraction = re.search(r"[.,](\d+)$", text)
        decimals = len(fraction.group(1)) if fraction else 0
    return {(2, False): 3, (2, True): 4, (0, True): 5}.get((decimals, thousands), 0)


def _to_number(digits: str) -> int | float | None:
    try:
        number = float(digits)
    except ValueError:
        return None
    if "." not in digits and abs(number) < 1e15:
        return int(digits)
    return number


# ---------- Excel ----------
_ILLEGAL_XML = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _column(index: int) -> str:
    letters = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        letters = chr(65 + rest) + letters
    return letters


def sheet_names(tables: list[Table], title: str = "Página {page}") -> list[str]:
    """Nombres de hoja válidos y únicos (Excel admite 31 caracteres y prohíbe []:*?/\\)."""
    names: list[str] = []
    per_page: dict[int, int] = {}
    for table in tables:
        per_page[table.page] = per_page.get(table.page, 0) + 1
    for table in tables:
        name = title.format(page=table.page + 1)
        if per_page[table.page] > 1:
            name += f" ({table.index})"
        name = re.sub(r"[\[\]:*?/\\]", "-", name).strip("'")[:31] or "Hoja"
        base, counter = name, 2
        while name.lower() in (n.lower() for n in names):
            suffix = f" {counter}"
            name = base[:31 - len(suffix)] + suffix
            counter += 1
        names.append(name)
    return names


def _sheet_xml(table: Table, decimal: str) -> str:
    widths: dict[int, float] = {}
    lines = []
    for r, row in enumerate(table.rows):
        cells = []
        bold = table.header and r == 0
        for c, value in enumerate(row):
            if not value:
                continue
            ref = f"{_column(c)}{r + 1}"
            longest = max(len(line) for line in value.split("\n"))
            widths[c] = max(widths.get(c, 0), longest * (1.2 if bold else 1.1) + 2)
            number = None if bold else parse_number(value, decimal)
            if number is not None:
                style = _number_style(value, number)
                cells.append(f'<c r="{ref}"{f" s=\"{style}\"" if style else ""}><v>{number!r}</v></c>')
                continue
            style = 1 if bold else (2 if "\n" in value else 0)
            text = escape(_ILLEGAL_XML.sub("", value))
            cells.append(f'<c r="{ref}" t="inlineStr"{f" s=\"{style}\"" if style else ""}>'
                         f'<is><t xml:space="preserve">{text}</t></is></c>')
        lines.append(f'<row r="{r + 1}">{"".join(cells)}</row>')
    views = ('<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" '
             'state="frozen"/></sheetView></sheetViews>') if table.header else ""
    cols = "".join(f'<col min="{c + 1}" max="{c + 1}" width="{min(60.0, max(8.0, w)):.1f}" customWidth="1"/>'
                   for c, w in sorted(widths.items()))
    return (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<worksheet xmlns="{_NS}">{views}'
            f'{f"<cols>{cols}</cols>" if cols else ""}<sheetData>{"".join(lines)}</sheetData></worksheet>')


_STYLES = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="{_NS}"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="2"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="6"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf><xf numFmtId="2" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'''  # noqa: E501


def write_xlsx(tables: list[Table], output: str | os.PathLike, *, decimal: str = ",",
               sheet_title: str = "Página {page}") -> Path:
    if not tables:
        raise InvalidOptionError("no hay tablas", option="tables")
    names = sheet_names(tables, sheet_title)
    count = len(tables)
    types = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/'
                    f'vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, count + 1))
    parts = {
        "[Content_Types].xml": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/xl/workbook.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            '<Override PartName="/xl/styles.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f"{types}</Types>"),
        "_rels/.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>'),
        "xl/workbook.xml": (
            f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<workbook xmlns="{_NS}" xmlns:r="{_REL}">'
            "<sheets>" + "".join(f'<sheet name="{escape(name, {chr(34): "&quot;"})}" sheetId="{i}" r:id="rId{i}"/>'
                                 for i, name in enumerate(names, 1)) + "</sheets></workbook>"),
        "xl/_rels/workbook.xml.rels": (
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(f'<Relationship Id="rId{i}" Type="{_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                      for i in range(1, count + 1))
            + f'<Relationship Id="rId{count + 1}" Type="{_REL}/styles" Target="styles.xml"/></Relationships>'),
        "xl/styles.xml": _STYLES,
    }
    for i, table in enumerate(tables, 1):
        parts[f"xl/worksheets/sheet{i}.xml"] = _sheet_xml(table, decimal)

    def writer(tmp: str) -> None:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, content in parts.items():
                archive.writestr(name, content)

    return atomic_write(output, writer)


# ---------- CSV ----------
def _csv_safe(value: str) -> str:
    # Una celda que empieza por «=» sería una fórmula al abrir el CSV en una hoja de cálculo.
    if value[:1] in ("=", "+", "-", "@", "\t") and parse_number(value) is None:
        return "'" + value
    return value


def write_csv(tables: list[Table], output: str | os.PathLike, *, delimiter: str = ";") -> list[Path]:
    """Un CSV por tabla; con varias, «nombre_p3_1.csv», «nombre_p3_2.csv»..."""
    if not tables:
        raise InvalidOptionError("no hay tablas", option="tables")
    target = Path(output)
    if target.suffix.lower() != ".csv":
        target = target.with_name(target.name + ".csv")
    paths = []
    for table in tables:
        path = target if len(tables) == 1 else target.with_name(f"{target.stem}_p{table.page + 1}_{table.index}.csv")
        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=delimiter, lineterminator="\r\n")
        for row in table.rows:
            writer.writerow([_csv_safe(cell) for cell in row])
        # Con BOM, Excel reconoce las tildes (UTF-8) al abrirlo con doble clic.
        data = ("﻿" + buffer.getvalue()).encode("utf-8")
        paths.append(atomic_write(path, lambda tmp, d=data: Path(tmp).write_bytes(d)))
    return paths


def export_tables(
    source: PdfSource,
    output: str | os.PathLike,
    *,
    fmt: str = "xlsx",
    pages: str = "",
    decimal: str = ",",
    sheet_title: str = "Página {page}",
    password: str | None = None,
    progress: ProgressCallback | None = None,
) -> TablesResult:
    """Busca las tablas y las guarda; si no hay ninguna, no escribe nada."""
    if fmt not in FORMATS:
        raise InvalidOptionError(fmt, option="fmt")
    tables = find_tables(source, pages, password=password, progress=progress)
    if not tables:
        return TablesResult()
    if fmt == "xlsx":
        target = Path(output)
        if target.suffix.lower() != ".xlsx":
            target = target.with_name(target.name + ".xlsx")
        return TablesResult([write_xlsx(tables, target, decimal=decimal, sheet_title=sheet_title)], len(tables))
    delimiter = ";" if decimal == "," else ","
    return TablesResult(write_csv(tables, output, delimiter=delimiter), len(tables))
