import pytest

from blackpdf.core.errors import InvalidRangeError
from blackpdf.core.ranges import (
    chunk_indices,
    format_page_ranges,
    is_valid_spec,
    parse_page_ranges,
    parse_page_selection,
)


def test_parse_mixed_ranges():
    assert parse_page_ranges("1-3,5,8-10", 10) == [[0, 1, 2], [4], [7, 8, 9]]


def test_parse_open_and_reverse_ranges():
    assert parse_page_ranges("8-", 10) == [[7, 8, 9]]
    assert parse_page_ranges("-2", 10) == [[0, 1]]
    assert parse_page_ranges("3-1", 10) == [[2, 1, 0]]
    assert parse_page_ranges("fin", 4) == [[3]]
    assert parse_page_ranges("2-fin", 4) == [[1, 2, 3]]


def test_parse_accepts_spaces_and_semicolons():
    assert parse_page_ranges(" 1 ; 3 - 4 ", 5) == [[0], [2, 3]]
    assert parse_page_ranges("1 2,3", 5) == [[0], [1], [2]]
    assert parse_page_ranges("1\u20133", 5) == [[0, 1, 2]]


@pytest.mark.parametrize("spec", ["", "0", "11", "a", "1-x", "2--3", "1-11"])
def test_parse_rejects_invalid(spec):
    with pytest.raises(InvalidRangeError):
        parse_page_ranges(spec, 10)
    assert not is_valid_spec(spec, 10)


def test_selection_deduplicates_and_keeps_order():
    assert parse_page_selection("3,1-3", 5) == [2, 0, 1]


def test_selection_empty_means_all_when_allowed():
    assert parse_page_selection("", 3, allow_empty_all=True) == [0, 1, 2]
    with pytest.raises(InvalidRangeError):
        parse_page_selection("", 3)


def test_format_ranges():
    assert format_page_ranges([0, 1, 2, 4, 7, 8]) == "1-3,5,8-9"
    assert format_page_ranges([]) == ""
    assert format_page_ranges([5]) == "6"


def test_chunk_indices():
    assert chunk_indices(7, 3) == [[0, 1, 2], [3, 4, 5], [6]]
    with pytest.raises(InvalidRangeError):
        chunk_indices(7, 0)
