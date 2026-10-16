import pytest

from blackpdf.core.history import History


def test_undo_redo_cycle():
    history = History()
    history.push("rotar", b"v1")
    assert history.can_undo() and not history.can_redo()
    assert history.undo_label() == "rotar"
    assert history.undo(b"v2") == b"v1"
    assert history.can_redo()
    assert history.redo_label() == "rotar"
    assert history.redo(b"v1") == b"v2"
    assert history.can_undo()


def test_push_clears_redo():
    history = History()
    history.push("a", b"1")
    history.undo(b"2")
    history.push("b", b"3")
    assert not history.can_redo()


def test_limit_is_respected():
    history = History(limit=30)
    for i in range(40):
        history.push(str(i), bytes([i]))
    assert len(history) == 30
    restored = []
    current = b"now"
    while history.can_undo():
        current = history.undo(current)
        restored.append(current)
    assert restored[-1] == bytes([10])


def test_invalid_limit():
    with pytest.raises(ValueError):
        History(limit=0)


def test_clear():
    history = History()
    history.push("a", b"1")
    history.clear()
    assert not history.can_undo() and not history.can_redo()
