import pymupdf
import pytest

from blackpdf.core.errors import EmptyInputError, InvalidOptionError, LastPageError
from blackpdf.core.pages import (
    delete_pages,
    duplicate_pages,
    extract_document,
    insert_blank_page,
    insert_pdf_pages,
    move_pages,
    reorder_pages,
    rotate_pages,
    rotate_pdf,
)

from .conftest import build_pdf, page_labels


def test_rotate_pages_accumulates():
    doc = build_pdf(3)
    rotate_pages(doc, [0, 2], 90)
    rotate_pages(doc, [0], 90)
    assert [p.rotation for p in doc] == [180, 0, 90]
    rotate_pages(doc, [2], -90)
    assert doc[2].rotation == 0


def test_rotate_rejects_odd_angles():
    with pytest.raises(InvalidOptionError):
        rotate_pages(build_pdf(1), [0], 45)


def test_delete_pages():
    doc = build_pdf(4)
    delete_pages(doc, [1, 3])
    assert page_labels(doc) == ["P1", "P3"]


def test_delete_all_pages_is_refused():
    doc = build_pdf(2)
    with pytest.raises(LastPageError):
        delete_pages(doc, [0, 1])


def test_delete_requires_selection():
    with pytest.raises(EmptyInputError):
        delete_pages(build_pdf(2), [])


def test_duplicate_pages():
    doc = build_pdf(3)
    new = duplicate_pages(doc, [0, 2])
    assert page_labels(doc) == ["P1", "P1", "P2", "P3", "P3"]
    assert new == [1, 4]


def test_duplicate_is_independent():
    doc = build_pdf(1)
    duplicate_pages(doc, [0])
    doc[1].add_rect_annot(pymupdf.Rect(10, 10, 50, 50))
    assert len(list(doc[0].annots())) == 0


@pytest.mark.parametrize(
    "selection,target,expected,new_indices",
    [
        ([0], 3, ["P2", "P3", "P1", "P4"], [2]),
        ([3], 0, ["P4", "P1", "P2", "P3"], [0]),
        ([0, 2], 4, ["P2", "P4", "P1", "P3"], [2, 3]),
        ([1, 3], 1, ["P1", "P2", "P4", "P3"], [1, 2]),
        ([1], 1, ["P1", "P2", "P3", "P4"], [1]),
    ],
)
def test_move_pages(selection, target, expected, new_indices):
    doc = build_pdf(4)
    assert move_pages(doc, selection, target) == new_indices
    assert page_labels(doc) == expected


def test_reorder_pages():
    doc = build_pdf(3)
    reorder_pages(doc, [2, 0, 1])
    assert page_labels(doc) == ["P3", "P1", "P2"]
    with pytest.raises(InvalidOptionError):
        reorder_pages(doc, [0, 0, 1])


def test_insert_blank_page_uses_neighbour_size():
    doc = build_pdf(2, size=(300, 400))
    index = insert_blank_page(doc, 1)
    assert index == 1 and doc.page_count == 3
    assert (doc[1].rect.width, doc[1].rect.height) == (300, 400)
    assert doc[1].get_text().strip() == ""
    insert_blank_page(doc, 99, 200, 200)
    assert doc[-1].rect.width == 200


def test_insert_pdf_pages(make_pdf):
    other = make_pdf(pages=3, prefix="Q")
    doc = build_pdf(2)
    new = insert_pdf_pages(doc, other, 1, pages=[2, 0])
    assert new == [1, 2]
    labels = [label for label in page_labels(doc)]
    assert doc.page_count == 4
    assert doc[1].get_text().startswith("Q3")
    assert doc[2].get_text().startswith("Q1")
    assert labels[0] == "P1" and labels[3] == "P2"


def test_insert_pdf_at_end(make_pdf):
    doc = build_pdf(1)
    insert_pdf_pages(doc, make_pdf(pages=2, prefix="Q"), doc.page_count)
    assert doc[2].get_text().startswith("Q2")


def test_extract_document_keeps_order():
    out = extract_document(build_pdf(4), [3, 0])
    assert page_labels(out) == ["P4", "P1"]


def test_rotate_pdf_tool(make_pdf, tmp_path):
    output = tmp_path / "girado.pdf"
    rotate_pdf(make_pdf(pages=3), output, 270, pages="2-3")
    doc = pymupdf.open(output)
    assert [p.rotation for p in doc] == [0, 270, 270]
