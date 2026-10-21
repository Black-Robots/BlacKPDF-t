"""Tablas a Excel y CSV."""

from __future__ import annotations

import csv
import zipfile
from xml.etree import ElementTree

import pymupdf
import pytest

from blackpdf.core import tables
from blackpdf.core.errors import InvalidOptionError

ROWS = [
    ["Partida", "Cantidad", "Precio", "Total"],
    ["Ladrillo", "1.200", "0,35", "420,00"],
    ["Cemento", "40", "6,10", "244,00"],
    ["=HIPERVINCULO()", "16", "22,50", "1.360,50"],
]
NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _pdf(pages_with_table=(0,), pages: int = 2) -> bytes:
    doc = pymupdf.open()
    for number in range(pages):
        page = doc.new_page()
        page.insert_text((72, 60), f"Página {number + 1}: texto corriente, sin tabla.", fontsize=11)
        if number not in pages_with_table:
            continue
        for r, row in enumerate(ROWS):
            for c, text in enumerate(row):
                rect = pymupdf.Rect(72 + c * 110, 100 + r * 30, 72 + (c + 1) * 110, 100 + (r + 1) * 30)
                page.draw_rect(rect, color=(0, 0, 0), width=0.7)
                page.insert_text((rect.x0 + 5, rect.y0 + 19), text, fontsize=10,
                                 fontname="hebo" if r == 0 else "helv")
    return doc.tobytes()


def test_parse_number():
    cases = {"1.234,56": 1234.56, "1,234.56": 1234.56, "-12": -12, "0,35": 0.35, "3.5": 3.5,
             "1 234 567": 1234567, "1.234.567": 1234567, "−7,5": -7.5}
    for text, value in cases.items():
        assert tables.parse_number(text) == value, text
    assert tables.parse_number("1.200", decimal=",") == 1200, "con coma decimal, el punto agrupa miles"
    assert tables.parse_number("1.200", decimal=".") == 1.2
    for text in ("", "12 %", "abc", "1.2.3,4,5", "12/10/2026", "B-12"):
        assert tables.parse_number(text) is None, text


def test_find_tables_only_where_there_are():
    found = tables.find_tables(_pdf(pages_with_table=(1,), pages=3))
    assert len(found) == 1 and found[0].page == 1 and found[0].header
    assert found[0].rows == ROWS
    assert tables.find_tables(_pdf(), pages="2") == [], "solo en las páginas pedidas"


def test_export_xlsx_one_sheet_per_table(tmp_path):
    result = tables.export_tables(_pdf(pages_with_table=(0, 1)), tmp_path / "obra", sheet_title="Pág. {page}")
    assert result.tables == 2 and result.paths == [tmp_path / "obra.xlsx"]
    with zipfile.ZipFile(result.paths[0]) as archive:
        assert archive.testzip() is None
        workbook = ElementTree.fromstring(archive.read("xl/workbook.xml"))
        assert [s.get("name") for s in workbook.iterfind(".//m:sheet", NS)] == ["Pág. 1", "Pág. 2"]
        sheet = ElementTree.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    cells = {c.get("r"): c for c in sheet.iterfind(".//m:c", NS)}
    assert cells["A1"].get("s") == "1" and cells["A1"].find("m:is/m:t", NS).text == "Partida", "títulos en negrita"
    assert cells["B2"].find("m:v", NS).text == "1200" and cells["C2"].find("m:v", NS).text == "0.35"
    assert cells["D4"].find("m:v", NS).text == "1360.5", "los importes, como números"
    assert cells["D4"].get("s") == "4" and cells["D2"].get("s") == "3" and cells["B2"].get("s") == "5", \
        "y se ven como en el PDF: «1.360,50», «420,00», «1.200»"
    assert cells["B3"].get("s") is None
    assert cells["A4"].get("t") == "inlineStr", "en Excel, «=...» es texto y no una fórmula"
    assert sheet.find(".//m:pane", NS).get("state") == "frozen"


def test_export_csv_for_spreadsheets(tmp_path):
    result = tables.export_tables(_pdf(), tmp_path / "obra.csv", fmt="csv")
    assert result.paths == [tmp_path / "obra.csv"]
    data = result.paths[0].read_bytes()
    assert data.startswith(b"\xef\xbb\xbf"), "con BOM, para que Excel lea bien las tildes"
    rows = list(csv.reader(data.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert rows[1] == ["Ladrillo", "1.200", "0,35", "420,00"]
    assert rows[3][0] == "'=HIPERVINCULO()", "nunca una fórmula al abrirlo"
    several = tables.export_tables(_pdf(pages_with_table=(0, 1)), tmp_path / "varias.csv", fmt="csv", decimal=".")
    assert [p.name for p in several.paths] == ["varias_p1_1.csv", "varias_p2_1.csv"]
    assert "," in several.paths[0].read_text("utf-8-sig").splitlines()[0]


def test_no_tables_writes_nothing(tmp_path):
    result = tables.export_tables(_pdf(pages_with_table=()), tmp_path / "nada.xlsx")
    assert result.tables == 0 and not result.paths and not list(tmp_path.iterdir())
    with pytest.raises(InvalidOptionError):
        tables.export_tables(_pdf(), tmp_path / "x", fmt="ods")


def test_sheet_names_are_valid_and_unique():
    items = [tables.Table(0, 1, [["a", "b"]] * 2), tables.Table(0, 2, [["a", "b"]] * 2),
             tables.Table(4, 1, [["a", "b"]] * 2)]
    assert tables.sheet_names(items, "Página {page}") == ["Página 1 (1)", "Página 1 (2)", "Página 5"]
    long = tables.sheet_names(items[:1] * 2, "Una hoja con un nombre demasiado largo: [{page}]")
    assert all(len(name) <= 31 and "[" not in name and ":" not in name for name in long)
    assert long[0] != long[1]
