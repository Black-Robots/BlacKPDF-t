import pymupdf
import pytest

from blackpdf.core.annotations import add_shape, add_text_markup
from blackpdf.core.comments import (
    add_page_comment,
    add_reply,
    count_comments,
    delete_comment,
    list_comments,
    page_comments,
    set_comment,
)
from blackpdf.core.errors import InvalidOptionError
from blackpdf.core.text import line_rects, page_words, select_words

from .conftest import build_pdf


def test_add_page_comment_and_list():
    doc = build_pdf(2)
    xref = add_page_comment(doc[1], "Revisar cifras", "Ana")
    comments = list_comments(doc)
    assert len(comments) == 1
    comment = comments[0]
    assert comment.page == 1 and comment.xref == xref
    assert comment.content == "Revisar cifras" and comment.author == "Ana"
    assert comment.kind == "Text"
    assert comment.modified, "lleva fecha"
    assert comment.rect.x0 > doc[1].rect.width - 60, "por defecto en la esquina superior derecha"


def test_default_comments_do_not_overlap():
    doc = build_pdf(1)
    add_page_comment(doc[0], "uno", "Ana")
    add_page_comment(doc[0], "dos", "Ana")
    first, second = page_comments(doc[0])
    assert abs(first.rect.y0 - second.rect.y0) >= 20


def test_highlight_quote_and_comment():
    doc = build_pdf(1)
    page = doc[0]
    words = select_words(page_words(page), pymupdf.Point(70, 130), pymupdf.Point(200, 135))
    annot = add_text_markup(page, "highlight", line_rects(words), (1, 0.8, 0))
    comments = page_comments(page)
    assert comments[0].kind == "Highlight"
    assert comments[0].quote.startswith("texto de")
    assert comments[0].content == ""
    set_comment(page, annot.xref, "Importante", author="Luis")
    updated = page_comments(page)[0]
    assert updated.content == "Importante" and updated.author == "Luis"


def test_replies_are_nested_and_deleted_with_parent():
    doc = build_pdf(1)
    page = doc[0]
    parent = add_page_comment(page, "¿Es correcto?", "Ana")
    reply = add_reply(page, parent, "Sí, revisado", "Luis")
    add_reply(page, reply, "Gracias", "Ana")
    roots = page_comments(page)
    assert len(roots) == 1
    assert roots[0].replies[0].content == "Sí, revisado"
    assert roots[0].replies[0].replies[0].content == "Gracias"
    assert count_comments(doc) == 3
    assert delete_comment(page, parent) == 3
    assert count_comments(doc) == 0


def test_comments_survive_save(tmp_path):
    doc = build_pdf(1)
    parent = add_page_comment(doc[0], "Nota", "Ana")
    add_reply(doc[0], parent, "Respuesta", "Luis")
    path = tmp_path / "c.pdf"
    doc.save(path)
    reopened = pymupdf.open(path)
    roots = page_comments(reopened[0])
    assert roots[0].content == "Nota" and roots[0].replies[0].author == "Luis"


def test_shapes_are_commentable_and_rotation_is_visual():
    doc = build_pdf(1)
    page = doc[0]
    page.set_rotation(90)
    add_shape(page, "rect", (20, 20), (80, 60))
    comment = page_comments(page)[0]
    assert comment.kind == "Square"
    assert comment.rect.x0 == pytest.approx(19, abs=2)


def test_comment_validation():
    doc = build_pdf(1)
    with pytest.raises(InvalidOptionError):
        add_page_comment(doc[0], "   ", "Ana")
    with pytest.raises(InvalidOptionError):
        set_comment(doc[0], 99999, "x")
    with pytest.raises(InvalidOptionError):
        delete_comment(doc[0], 99999)
