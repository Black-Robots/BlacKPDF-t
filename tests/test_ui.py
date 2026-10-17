"""Pruebas de humo de la interfaz (sin pantalla): flujos reales con ratón simulado."""

from __future__ import annotations

import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pymupdf
import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, QSettings, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from .conftest import build_pdf, page_labels


@pytest.fixture(scope="session")
def qapp(tmp_path_factory):
    settings_dir = tmp_path_factory.mktemp("settings")
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(settings_dir))
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    from blackpdf.ui import theme

    theme.load_fonts()
    theme.manager().apply("dark")
    yield app


def pump(app, seconds: float = 0.2) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def wait_until(app, condition, timeout: float = 30.0) -> bool:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def window(qapp):
    from blackpdf.ui.main_window import MainWindow

    win = MainWindow()
    win.resize(1280, 860)
    win.show()
    pump(qapp, 0.2)
    yield win
    for tab in win.document_tabs():
        tab.document._modified = False
    win.close()
    win.deleteLater()
    pump(qapp, 0.05)


@pytest.fixture
def opened(qapp, window, tmp_path):
    path = tmp_path / "doc.pdf"
    doc = build_pdf(4)
    doc.save(path)
    window.open_files([str(path)])
    pump(qapp, 0.4)
    tab = window.current_tab()
    assert tab is not None
    return window, tab, path


def mouse(widget, kind, pos: QPointF, buttons=Qt.MouseButton.LeftButton):
    event_type = {"press": QEvent.Type.MouseButtonPress, "move": QEvent.Type.MouseMove,
                  "release": QEvent.Type.MouseButtonRelease}[kind]
    button = Qt.MouseButton.NoButton if kind == "move" else Qt.MouseButton.LeftButton
    held = Qt.MouseButton.NoButton if kind == "release" else buttons
    event = QMouseEvent(event_type, pos, widget.mapToGlobal(pos.toPoint()).toPointF(), button, held,
                        Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, event)


def drag(viewer, page: int, start_pt: QPointF, end_pt: QPointF, steps: int = 8) -> None:
    viewport = viewer.viewport()
    a = viewer.page_to_viewport(page, start_pt)
    b = viewer.page_to_viewport(page, end_pt)
    mouse(viewport, "press", a)
    for i in range(1, steps + 1):
        t = i / steps
        mouse(viewport, "move", QPointF(a.x() + (b.x() - a.x()) * t, a.y() + (b.y() - a.y()) * t))
    mouse(viewport, "release", b)


def annots(tab, page=0) -> list[str]:
    """Tipos de anotación de la página (se conserva la página mientras se leen)."""
    pg = tab.document.doc[page]
    return [a.type[1] for a in pg.annots()]


def test_open_document_creates_tab(opened):
    window, tab, path = opened
    assert tab.document.page_count == 4
    assert window.tab_bar.count() == 2
    window.open_files([str(path)])
    assert window.tab_bar.count() == 2, "abrir el mismo archivo reutiliza la pestaña"


def test_page_operations_with_undo(qapp, opened):
    window, tab, _ = opened
    tab.thumbnails.select_pages([0, 1])
    tab.rotate_pages(90)
    assert [p.rotation for p in tab.document.doc][:2] == [90, 90]
    tab.undo()
    assert [p.rotation for p in tab.document.doc][:2] == [0, 0]
    tab.redo()
    assert tab.document.doc[0].rotation == 90
    tab.thumbnails.select_pages([3])
    tab.delete_pages()
    assert tab.document.page_count == 3
    tab.thumbnails.select_pages([0])
    tab.duplicate_pages()
    assert tab.document.page_count == 4
    tab.move_pages([0], 4)
    assert page_labels(tab.document.doc)[-1] == "P1"
    tab.insert_blank(after=True)
    assert tab.document.page_count == 5
    assert tab.document.is_modified


def test_thumbnail_drop_reorders(qapp, opened):
    _, tab, _ = opened
    tab.thumb_model.movePages.emit([3], 0)
    pump(qapp, 0.05)
    assert page_labels(tab.document.doc) == ["P4", "P1", "P2", "P3"]


def _drop(view, mime, pos):
    from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent

    viewport = view.viewport()
    point = QPointF(pos)
    actions = Qt.DropAction.MoveAction | Qt.DropAction.CopyAction
    for cls in (QDragEnterEvent, QDragMoveEvent):
        QApplication.sendEvent(viewport, cls(pos, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))
    QApplication.sendEvent(viewport, QDropEvent(point, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier))


def test_drag_thumbnail_to_new_position(qapp, opened):
    _, tab, _ = opened
    view = tab.thumbnails
    model = tab.thumb_model
    pump(qapp, 0.2)
    mime = model.mimeData([model.index(3, 0)])
    first = view.visualRect(model.index(0, 0))
    _drop(view, mime, QPoint(first.center().x(), first.top() + 4))
    assert page_labels(tab.document.doc) == ["P4", "P1", "P2", "P3"]
    mime = model.mimeData([model.index(0, 0), model.index(1, 0)])
    last = view.visualRect(model.index(3, 0))
    _drop(view, mime, QPoint(last.center().x(), last.bottom() - 4))
    assert page_labels(tab.document.doc) == ["P2", "P3", "P4", "P1"]


def test_drop_pdf_file_on_thumbnails_inserts_pages(qapp, opened, tmp_path):
    from PySide6.QtCore import QMimeData, QUrl

    _, tab, _ = opened
    other = tmp_path / "otro.pdf"
    build_pdf(2, prefix="Q").save(other)
    view = tab.thumbnails
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(other))])
    second = view.visualRect(tab.thumb_model.index(1, 0))
    _drop(view, mime, QPoint(second.center().x(), second.top() + 4))
    doc = tab.document.doc
    assert doc.page_count == 6
    assert doc[1].get_text().startswith("Q1") and doc[2].get_text().startswith("Q2")
    tab.undo()
    assert tab.document.page_count == 4


def test_pen_and_shapes_create_annotations(qapp, opened):
    _, tab, _ = opened
    viewer = tab.viewer
    tab.set_tool("pen")
    drag(viewer, 0, QPointF(100, 300), QPointF(250, 360))
    assert annots(tab) == ["Ink"]
    tab.set_tool("rect")
    drag(viewer, 0, QPointF(100, 400), QPointF(220, 480))
    tab.set_tool("arrow")
    drag(viewer, 0, QPointF(300, 400), QPointF(420, 480))
    assert annots(tab) == ["Ink", "Square", "Line"]
    tab.undo()
    assert len(annots(tab)) == 2


def test_highlight_selected_text(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("highlight")
    drag(tab.viewer, 0, QPointF(74, 134), QPointF(240, 136))
    assert annots(tab) == ["Highlight"]


def test_eraser_removes_annotation(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("rect")
    drag(tab.viewer, 0, QPointF(100, 400), QPointF(220, 480))
    assert len(annots(tab)) == 1
    tab.set_tool("eraser")
    pos = tab.viewer.page_to_viewport(0, QPointF(160, 440))
    mouse(tab.viewer.viewport(), "move", pos, Qt.MouseButton.NoButton)
    mouse(tab.viewer.viewport(), "press", pos)
    mouse(tab.viewer.viewport(), "release", pos)
    assert len(annots(tab)) == 0


def test_text_selection_and_copy(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("select")
    drag(tab.viewer, 0, QPointF(70, 95), QPointF(300, 140))
    text = tab.viewer.selected_text()
    assert "P1" in text and "texto" in text
    tab.copy()
    assert "P1" in QApplication.clipboard().text()


def test_save_keeps_annotations(qapp, opened, tmp_path):
    _, tab, path = opened
    tab.set_tool("pen")
    drag(tab.viewer, 0, QPointF(100, 300), QPointF(250, 360))
    assert tab._save_to(str(path))
    assert not tab.document.is_modified
    saved = pymupdf.open(path)
    assert len(list(saved[0].annots())) == 1


def test_search_finds_all_pages(qapp, opened):
    _, tab, _ = opened
    tab.find()
    tab.search_controller.start("ejemplo")
    assert wait_until(qapp, lambda: len(tab.search_controller.hits) == 4, 5)
    tab.search_controller.next()
    assert tab.search_controller.current == 1


def test_zoom_and_fit(qapp, opened):
    _, tab, _ = opened
    viewer = tab.viewer
    viewer.set_zoom(2.0)
    assert viewer.zoom == pytest.approx(2.0)
    viewer.zoom_out()
    assert viewer.zoom < 2.0
    viewer.set_fit_mode("page")
    pump(qapp, 0.1)
    rect = viewer.page_rect(viewer.current_page)
    assert rect.height() <= viewer.viewport().height()


def test_tool_dialog_runs_in_background(qapp, opened, tmp_path):
    window, tab, _ = opened
    from blackpdf.ui import tools

    dialog = tools.dialog_class("compress")(window, window._context_for(tab), {})
    output = tmp_path / "comprimido.pdf"
    dialog.output.picker.set_path(str(output))
    dialog.run()
    assert wait_until(qapp, lambda: dialog.pages.currentWidget() is dialog.result_panel, 60)
    assert output.exists() and pymupdf.open(output).page_count == 4
    dialog.close()


def test_tool_dialog_reports_errors(qapp, opened, tmp_path, monkeypatch):
    window, tab, _ = opened
    from blackpdf.ui import tools
    from blackpdf.ui.dialogs import base

    shown = []
    monkeypatch.setattr(base, "show_error", lambda parent, exc: shown.append(exc))
    dialog = tools.dialog_class("extract")(window, window._context_for(tab), {})
    dialog.ranges.setText("1-2")
    dialog.ranges.page_count = 0  # salta la validación del formulario para forzar el error en el subproceso
    dialog.ranges.setText("9")
    dialog.output.picker.set_path(str(tmp_path / "x.pdf"))
    dialog.run()
    assert wait_until(qapp, lambda: bool(shown), 60)
    assert type(shown[0]).__name__ == "InvalidRangeError"
    dialog.close()


def test_command_palette_scoring():
    from blackpdf.ui.command_palette import Command, score

    merge = Command("Unir PDF", "Herramientas", "combine", lambda: None)
    compress = Command("Comprimir PDF", "Herramientas", "shrink", lambda: None)
    assert score("unir", merge) > score("unir", compress)
    assert score("cmprm", compress) > 0
    assert score("xyz", merge) == 0


def test_palette_opens_and_runs(qapp, opened):
    window, tab, _ = opened
    window.show_palette()
    pump(qapp, 0.05)
    window.palette_overlay.edit.setText("girar páginas a la derecha")
    item = window.palette_overlay.list.item(0)
    window.palette_overlay._run_item(item)
    assert tab.document.doc[0].rotation == 90


def test_home_tools_and_theme(qapp, window):
    from blackpdf.ui import theme

    window.toggle_theme()
    assert not theme.current().is_dark
    window.toggle_theme()
    assert theme.current().is_dark
    assert window.home.recent_grid is not None


def test_organizer_mode_reorders_and_acts(qapp, opened):
    _, tab, _ = opened
    tab.set_organize_mode(True)
    pump(qapp, 0.3)
    assert tab.view_stack.currentWidget() is tab.organizer_panel
    view = tab.organizer
    model = tab.thumb_model
    mime = model.mimeData([model.index(0, 0)])
    last = view.visualRect(model.index(3, 0))
    _drop(view, mime, QPoint(last.right() - 4, last.center().y()))
    assert page_labels(tab.document.doc) == ["P2", "P3", "P4", "P1"]
    view.rotateRequested.emit([1], 90)
    assert tab.document.doc[1].rotation == 90
    view.select_pages([0, 1])
    tab.delete_pages()
    assert tab.document.page_count == 2
    view.openPage.emit(1)
    assert tab.view_stack.currentWidget() is tab.area
    assert tab.viewer.current_page == 1


def test_redact_tool_removes_content(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("redact")
    drag(tab.viewer, 0, QPointF(60, 120), QPointF(400, 150))
    text = tab.document.doc[0].get_text()
    assert "ejemplo" not in text and "P1" in text
    tab.undo()
    assert "ejemplo" in tab.document.doc[0].get_text()


def test_navigation_history(qapp, opened):
    _, tab, _ = opened
    viewer = tab.viewer
    viewer.go_to_page(0, smooth=False)
    pump(qapp, 0.05)
    viewer.jump_to_page(3)
    pump(qapp, 0.3)
    assert viewer.current_page == 3
    assert viewer.go_back()
    pump(qapp, 0.3)
    assert viewer.current_page == 0
    assert viewer.go_forward()
    assert viewer.current_page == 3


def test_reading_position_is_restored(qapp, window, tmp_path):
    from blackpdf.ui.settings import settings

    path = tmp_path / "largo.pdf"
    build_pdf(6).save(path)
    settings().set_reading_position(str(path), 4, 1.0, "auto")
    window.open_files([str(path)])
    pump(qapp, 0.5)
    tab = window.current_tab()
    assert tab.viewer.current_page == 4
    tab.viewer.go_to_page(2, smooth=False)
    pump(qapp, 0.1)
    window.close_current_tab()
    assert settings().reading_position(str(path))["page"] == 2
