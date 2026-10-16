import pymupdf

from blackpdf.core.files import human_size, safe_filename, unique_path, with_suffix_name
from blackpdf.core.navigation import get_outline, page_links, page_text, search_page
from blackpdf.core.text import FONT_CHOICES, font_for, line_rects, page_words, select_words, words_text

from .conftest import build_pdf


def test_outline_tree():
    doc = build_pdf(4)
    doc.set_toc([[1, "Uno", 1], [2, "Uno.a", 2], [2, "Uno.b", 3], [1, "Dos", 4]])
    roots = get_outline(doc)
    assert [r.title for r in roots] == ["Uno", "Dos"]
    assert [c.title for c in roots[0].children] == ["Uno.a", "Uno.b"]
    assert roots[1].page == 3


def test_search_is_case_insensitive_and_visual():
    doc = build_pdf(1)
    page = doc[0]
    hits = search_page(page, "EJEMPLO")
    assert len(hits) == 1
    page.set_rotation(90)
    rotated = search_page(page, "ejemplo")[0]
    assert rotated.height > rotated.width, "en una página girada el resultado se ve vertical"
    assert search_page(page, "   ") == []


def test_links():
    doc = build_pdf(2)
    page = doc[0]
    page.insert_link({"kind": pymupdf.LINK_GOTO, "from": pymupdf.Rect(10, 10, 50, 30), "page": 1, "to": pymupdf.Point(0, 0)})
    page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(10, 40, 50, 60), "uri": "https://example.org"})
    page = doc.reload_page(page)
    links = page_links(page)
    assert any(l.page == 1 for l in links)
    assert any(l.uri == "https://example.org" for l in links)


def test_page_text_with_clip():
    page = build_pdf(1)[0]
    assert "P1" in page_text(page)
    assert page_text(page, pymupdf.Rect(0, 120, 600, 150)).startswith("texto")


def test_word_selection_and_text():
    page = build_pdf(1)[0]
    words = page_words(page)
    chosen = select_words(words, pymupdf.Point(60, 90), pymupdf.Point(500, 140))
    text = words_text(chosen)
    assert text.startswith("P1")
    assert "buscar" in text
    assert len(line_rects(chosen)) == 2
    assert select_words([], pymupdf.Point(0, 0), pymupdf.Point(1, 1)) == []


def test_fonts():
    font, name, file = font_for("helv")
    assert name == "helv" and file is None
    font, name, file = font_for("helv", text="日本")
    assert file and file.endswith(".ttf")
    font, name, file = font_for("inter")
    assert file and font.text_length("abc", fontsize=10) > 0
    assert "inter" in FONT_CHOICES


def test_file_helpers(tmp_path):
    assert safe_filename('a<b>:c?.pdf') == "a_b__c_.pdf"
    assert safe_filename("   ") == "documento"
    target = tmp_path / "x.pdf"
    assert unique_path(target) == target
    target.write_bytes(b"1")
    assert unique_path(target).name == "x (2).pdf"
    assert with_suffix_name(tmp_path / "doc.pdf", "comprimido").name == "doc_comprimido.pdf"
    assert human_size(512) == "512 B"
    assert human_size(2048) == "2,0 KB"
    assert human_size(5 * 1024 * 1024) == "5,0 MB"
