"""Pruebas de humo de la interfaz (sin pantalla): flujos reales con ratón simulado."""

from __future__ import annotations

import os
import sys
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
    assert tab.view_stack.currentWidget() is tab.view_splitter
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


def test_comments_panel_flow(qapp, opened):
    from blackpdf.core.comments import list_comments

    _, tab, _ = opened
    tab.show_comments()
    pump(qapp, 0.2)
    panel = tab.comments
    panel.start_page_comment()
    pump(qapp, 0.05)
    panel._composer.edit.setPlainText("Revisar esta página")
    panel._composer._submit()
    pump(qapp, 0.3)
    comments = list_comments(tab.document.doc)
    assert len(comments) == 1 and comments[0].content == "Revisar esta página"
    assert tab.sidebar_count.text() == "1"
    card = panel.body.findChildren(type(panel).__mro__[0].__module__ and __import__("blackpdf.ui.comments", fromlist=["CommentCard"]).CommentCard)[0]
    card.replyRequested.emit(card.comment, "Hecho")
    pump(qapp, 0.3)
    assert list_comments(tab.document.doc)[0].replies[0].content == "Hecho"
    card = panel.body.findChildren(__import__("blackpdf.ui.comments", fromlist=["CommentCard"]).CommentCard)[0]
    card.deleteRequested.emit(card.comment)
    pump(qapp, 0.3)
    assert list_comments(tab.document.doc) == []
    tab.undo()
    assert len(list_comments(tab.document.doc)) == 1


def test_highlight_and_comment_opens_editor(qapp, opened):
    _, tab, _ = opened
    tab.set_tool("select")
    drag(tab.viewer, 0, QPointF(74, 134), QPointF(240, 136))
    tab._markup_selection("highlight", comment=True)
    pump(qapp, 0.4)
    assert tab.sidebar_switch.value() == "comments"
    from blackpdf.ui.comments import CommentCard

    cards = tab.comments.body.findChildren(CommentCard)
    assert cards and cards[0].comment.kind == "Highlight"
    assert cards[0]._composer is not None, "se abre el editor del comentario"


def test_split_view_same_and_other_document(qapp, window, tmp_path):
    first = tmp_path / "a.pdf"
    second = tmp_path / "b.pdf"
    build_pdf(8).save(first)
    build_pdf(8, prefix="Q").save(second)
    window.open_files([str(second)])
    window.open_files([str(first)])
    pump(qapp, 0.4)
    tab = window.current_tab()
    tab.set_split(True)
    pump(qapp, 0.3)
    pane = tab.split_pane
    assert pane is not None and pane.model is tab.document
    assert tab.split_button.isChecked()
    names = [pane.chooser.itemText(i) for i in range(pane.chooser.count())]
    assert "b.pdf" in names
    other = next(m for _, m in window._open_documents() if m is not tab.document)
    pane.set_document(other)
    pump(qapp, 0.2)
    assert pane.model is other
    # sincronizado: mover el principal mueve el secundario manteniendo la diferencia de páginas
    pane.viewer.set_position(1, 0)
    tab.viewer.set_position(0, 0)
    pump(qapp, 0.05)
    pane.set_sync(True)
    assert pane._page_delta == 1
    tab.viewer.set_position(3, 0)
    pump(qapp, 0.05)
    assert pane.viewer.position()[0] == 4
    # cerrar la otra pestaña devuelve el panel a este documento
    other_tab = next(t for t in window.document_tabs() if t.document is other)
    window.close_tab(window._tabs.index(other_tab))
    pump(qapp, 0.2)
    assert pane.model is tab.document
    tab.set_split(False)
    assert tab.split_pane is None and not tab.split_button.isChecked()


def test_comment_flash_paints(qapp, opened):
    from PySide6.QtCore import QRectF

    _, tab, _ = opened
    tab._show_comment(0, QRectF(100, 100, 50, 30))
    pump(qapp, 0.1)
    image = tab.viewer.viewport().grab()
    assert not image.isNull()


def test_signed_document_banner_and_safe_save(qapp, window, tmp_path):
    from tests.test_signatures import PASSWORD, make_certificate
    from blackpdf.core.signatures import SignOptions, list_signatures, sign_pdf

    plain = tmp_path / "contrato.pdf"
    build_pdf(2).save(plain)
    signed = tmp_path / "contrato_firmado.pdf"
    sign_pdf(plain, signed, make_certificate(tmp_path / "c.p12"), PASSWORD, SignOptions())
    original = signed.read_bytes()
    window.open_files([str(signed)])
    tab = window.current_tab()
    assert wait_until(qapp, lambda: bool(tab._signatures), 60)
    assert tab.signature_banner.isVisible()
    assert tab.signature_banner.property("level") == "warning", "certificado de prueba: íntegro pero no de confianza"
    assert tab.save() and signed.read_bytes() == original, "guardar sin cambios no toca el archivo"
    tab.rotate_pages(90)
    assert "Has modificado" in tab.signature_banner.text.text()
    tab.undo()
    tab.document._modified = False
    assert list_signatures(signed)[0].intact


def test_certificate_dialog_signs_current_document(qapp, opened, tmp_path):
    from tests.test_signatures import PASSWORD, make_certificate
    from blackpdf.core.signatures import list_signatures
    from blackpdf.ui import tools

    window, tab, _ = opened
    cert = make_certificate(tmp_path / "yo.p12")
    dialog = tools.dialog_class("sign_certificate")(window, window._context_for(tab), {})
    dialog.certificate.set_path(str(cert))
    dialog.cert_password.edit.setText(PASSWORD)
    dialog._check_certificate()
    assert dialog.is_ready() and "GARCÍA" in dialog.cert_text.text()
    output = tmp_path / "firmado.pdf"
    dialog.output.picker.set_path(str(output))
    dialog.run()
    assert wait_until(qapp, lambda: dialog.pages.currentWidget() is dialog.result_panel, 90)
    status = list_signatures(output)[0]
    assert status.intact and status.valid and status.covers_whole_file
    dialog.close()


# ---------------------------------------------------------------------------
# Modelos 3D
# ---------------------------------------------------------------------------
MODEL_DATA = os.path.join(os.path.dirname(__file__), "data", "model3d")


def test_3d_model_on_page_opens_viewer_window(qapp, window):
    window.open_files([os.path.join(MODEL_DATA, "views.pdf")])
    pump(qapp, 0.4)
    tab = window.current_tab()
    models = tab.viewer.models(0)
    assert len(models) == 1
    item, rect = models[0]
    assert item.format == "U3D"
    requested = []
    tab.viewer.modelRequested.connect(requested.append)
    center = tab.viewer.page_to_viewport(0, rect.center())
    assert tab.viewer.activate_at(center)
    assert requested and requested[0].xref == item.xref
    pump(qapp, 0.2)
    windows = list(tab._model_windows.values())
    assert len(windows) == 1
    model_window = windows[0]
    # Sin pantalla no hay OpenGL: la ventana lo explica en lugar de fallar.
    assert model_window.overlay.isVisible()
    model_window.close()
    pump(qapp, 0.1)
    assert not tab._model_windows


def test_model3d_window_controls(qapp):
    from blackpdf.core import model3d
    from blackpdf.ui.model3d_window import Model3DWindow

    scene = model3d.load_file(os.path.join(MODEL_DATA, "VertexColor.u3d"))
    win = Model3DWindow("VertexColor.u3d")
    win.resize(1200, 760)
    win.show()
    win.set_scene(scene)
    pump(qapp, 0.1)
    assert win.parts.list.count() == len(scene.parts) == 3
    labels = [win.views_box.itemText(i) for i in range(win.views_box.count())]
    assert "Isométrica" in labels and "Frente" in labels
    for mode in ("outline", "wireframe", "transparent", "solid"):
        win.modes.set_value(mode)
        assert win.viewer.render_mode == mode
    win.section_button.setChecked(True)
    assert win.viewer.section_axis is not None
    win.section_button.setChecked(False)
    assert win.viewer.section_axis is None
    win.parts.select(1)
    win.parts._isolate_current()
    assert win.viewer.hidden == {0, 2}
    win.parts.set_all_visible(True)
    assert not win.viewer.hidden
    win.projection_button.setChecked(True)
    assert win.viewer.camera.orthographic
    win.close()
    pump(qapp, 0.05)


def test_open_model_file_from_main_window(qapp, window):
    tabs_before = window.tab_bar.count()
    window.open_files([os.path.join(MODEL_DATA, "box.u3d")])
    pump(qapp, 0.2)
    assert window.tab_bar.count() == tabs_before
    assert len(window._model_windows) == 1
    for win in list(window._model_windows.values()):
        win.close()
    pump(qapp, 0.05)


def test_closed_tab_ignores_theme_changes(qapp, opened):
    """Al cerrar una pestaña, cambiar el tema no debe tocar sus paneles destruidos."""
    from blackpdf.ui import theme

    window, tab, _ = opened
    tab.show_comments()
    pump(qapp, 0.1)
    errors = []
    previous = sys.excepthook
    sys.excepthook = lambda *info: errors.append(info)
    try:
        window.close_tab(window._tabs.index(tab))
        pump(qapp, 0.1)
        # Como en el bucle principal: las vistas de la pestaña se destruyen de verdad.
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        pump(qapp, 0.1)
        theme.manager().apply("light")
        pump(qapp, 0.1)
        theme.manager().apply("dark")
        pump(qapp, 0.1)
    finally:
        sys.excepthook = previous
    assert not errors
