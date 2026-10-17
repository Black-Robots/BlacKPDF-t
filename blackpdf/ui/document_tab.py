"""Pestaña de documento: barra de herramientas, panel lateral y visor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSplitter,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import pages as core_pages
from ..core.errors import BlackPdfError
from ..core.document import needs_password, document_info
from ..core.ranges import format_page_ranges
from . import icons, theme
from .annotation_bar import TOOL_SHORTCUTS, AnnotationBar
from .dialogs.common import PropertiesDialog, ask_password, confirm_discard
from .dialogs.signature import SignatureDialog
from .document import PdfDocument
from .errors import show_error
from .interaction import STYLED_TOOLS, RedactTool, SignatureTool, create_tool
from .outline import OutlinePanel
from .printing import print_document
from .render import PageRenderer
from .search import SearchBar, SearchController
from .settings import settings
from .thumbnails import OrganizerView, ThumbnailModel, ThumbnailView
from .viewer import PdfViewer
from .widgets import Button, IconButton, SegmentedControl, Toast, open_filter, pdf_filter, separator

ZOOM_PRESETS = (50, 75, 100, 125, 150, 200, 300, 400)


def shortcut_text(sequence: QKeySequence | str) -> str:
    seq = QKeySequence(sequence)
    return seq.toString(QKeySequence.SequenceFormat.NativeText)


class ViewerArea(QWidget):
    """Contenedor del visor con widgets flotantes encima."""

    def __init__(self, viewer: PdfViewer, parent=None):
        super().__init__(parent)
        self.viewer = viewer
        viewer.setParent(self)
        self.annotation_bar: AnnotationBar | None = None
        self.search_bar: SearchBar | None = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.viewer.setGeometry(self.rect())
        self.layout_overlays()

    def layout_overlays(self, animate: bool = False) -> None:
        if self.annotation_bar is not None and self.annotation_bar.isVisible():
            self.annotation_bar.place(self.width() - 14, 12, animate)
        if self.search_bar is not None and self.search_bar.isVisible():
            bar = self.search_bar
            bar.adjustSize()
            width = min(max(bar.sizeHint().width(), 380), self.width() - 32)
            bar.resize(width, bar.height())
            top = 12
            if self.annotation_bar is not None and self.annotation_bar.isVisible():
                top = self.annotation_bar.geometry().bottom() + 10
            bar.move(self.width() - width - 24, top)


class DocumentTab(QWidget):
    titleChanged = Signal()
    toolRequested = Signal(str, dict)
    openRequested = Signal(list)
    fullscreenRequested = Signal()

    def __init__(self, document: PdfDocument, parent=None):
        super().__init__(parent)
        self.document = document
        self.renderer = PageRenderer(document, self)
        self.viewer = PdfViewer(document, self.renderer)
        self._tools: dict[str, object] = {}
        self._tool_name = "select"
        self._sidebar_width = settings().sidebar_width

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(1)
        self.sidebar = self._build_sidebar()
        self.area = ViewerArea(self.viewer)
        self.organizer_panel = self._build_organizer()
        self.view_stack = QStackedWidget(self)
        self.view_stack.addWidget(self.area)
        self.view_stack.addWidget(self.organizer_panel)
        self._organizing = False
        self._sidebar_before_organize = True
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.view_stack)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self._sidebar_resized)
        root.addWidget(self.splitter, 1)

        self.search_controller = SearchController(document, self.viewer, self)
        self.search_bar = SearchBar(self.search_controller, self.area)
        self.search_bar.hide()
        self.search_bar.closed.connect(self.viewer.setFocus)
        self.search_bar.redactRequested.connect(self.redact_search_results)
        self.area.search_bar = self.search_bar

        self.annotation_bar = AnnotationBar(self.area)
        self.annotation_bar.toolSelected.connect(self.set_tool)
        self.annotation_bar.styleChanged.connect(self._style_changed)
        self.area.annotation_bar = self.annotation_bar
        self.annotation_bar.setVisible(settings().annotation_bar_visible)
        self.annotate_button.setChecked(self.annotation_bar.isVisible())

        self._sidebar_anim = QVariantAnimation(self)
        self._sidebar_anim.setDuration(theme.ANIMATION_MS)
        self._sidebar_anim.valueChanged.connect(self._apply_sidebar_width)

        self.viewer.zoomChanged.connect(self._on_zoom)
        self.viewer.currentPageChanged.connect(self._on_page)
        self.viewer.fitModeChanged.connect(self._on_fit_mode)
        self.viewer.contextMenuRequested.connect(self._viewer_menu)
        document.historyChanged.connect(self._update_actions)
        document.modifiedChanged.connect(lambda _: self.titleChanged.emit())
        document.pathChanged.connect(lambda _: self.titleChanged.emit())
        document.changed.connect(self._on_document_changed)

        self.set_tool("select")
        self.set_night_mode(settings().night_mode)
        self._install_shortcuts()
        self._update_actions()
        self._on_page(0)
        self._on_zoom(self.viewer.zoom)
        self._on_fit_mode(self.viewer.fit_mode)
        QTimer.singleShot(0, self._initial_layout)

    # ======================================================================
    # Construcción
    # ======================================================================
    def _button(self, icon: str, tip: str, slot, shortcut: str = "", checkable: bool = False) -> IconButton:
        text = f"{tip} ({shortcut_text(shortcut)})" if shortcut else tip
        button = IconButton(icon, text, self, checkable=checkable)
        button.clicked.connect(slot)
        return button

    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("DocToolbar")
        bar.setFixedHeight(48)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(2)

        self.sidebar_button = self._button("panel-left", self.tr("Panel lateral"), self.toggle_sidebar, "F4", True)
        layout.addWidget(self.sidebar_button)
        self.organize_button = self._button("layout-grid", self.tr("Organizar páginas"), self.toggle_organize, "Ctrl+G", True)
        layout.addWidget(self.organize_button)
        layout.addSpacing(4)
        layout.addWidget(separator(True))
        layout.addSpacing(4)
        self.save_button = self._button("save", self.tr("Guardar"), self.save, "Ctrl+S")
        self.undo_button = self._button("undo-2", self.tr("Deshacer"), self.undo, "Ctrl+Z")
        self.redo_button = self._button("redo-2", self.tr("Rehacer"), self.redo, "Ctrl+Y")
        for button in (self.save_button, self.undo_button, self.redo_button):
            layout.addWidget(button)
        layout.addSpacing(4)
        layout.addWidget(separator(True))
        layout.addSpacing(8)

        self.page_edit = QLineEdit(bar)
        self.page_edit.setFixedWidth(52)
        self.page_edit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_edit.setToolTip(self.tr("Ir a la página"))
        self.page_edit.returnPressed.connect(self._jump_to_page)
        layout.addWidget(self.page_edit)
        self.page_total = QLabel("", bar)
        self.page_total.setProperty("role", "muted")
        layout.addSpacing(6)
        layout.addWidget(self.page_total)
        layout.addStretch(1)

        self.zoom_out_button = self._button("zoom-out", self.tr("Alejar"), self.viewer.zoom_out, "Ctrl+-")
        layout.addWidget(self.zoom_out_button)
        self.zoom_box = QComboBox(bar)
        self.zoom_box.setEditable(True)
        self.zoom_box.setFixedWidth(112)
        self.zoom_box.setObjectName("ZoomBox")
        self.zoom_box.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        for value in ZOOM_PRESETS:
            self.zoom_box.addItem(f"{value} %", value / 100)
        self.zoom_box.activated.connect(lambda i: self.viewer.set_zoom(self.zoom_box.itemData(i)))
        self.zoom_box.lineEdit().returnPressed.connect(self._zoom_from_text)
        self.zoom_box.lineEdit().setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.zoom_box)
        self.zoom_in_button = self._button("zoom-in", self.tr("Acercar"), self.viewer.zoom_in, "Ctrl++")
        layout.addWidget(self.zoom_in_button)
        self.fit_width_button = self._button(
            "move-horizontal", self.tr("Ajustar al ancho"), lambda: self.viewer.set_fit_mode("width"), "Ctrl+1", True
        )
        self.fit_page_button = self._button(
            "scan", self.tr("Página completa"), lambda: self.viewer.set_fit_mode("page"), "Ctrl+2", True
        )
        layout.addWidget(self.fit_width_button)
        layout.addWidget(self.fit_page_button)
        layout.addSpacing(4)
        layout.addWidget(separator(True))
        layout.addSpacing(4)
        self.rotate_left_button = self._button("rotate-ccw", self.tr("Girar a la izquierda"), lambda: self.rotate_pages(-90), "Ctrl+Shift+L")
        self.rotate_right_button = self._button("rotate-cw", self.tr("Girar a la derecha"), lambda: self.rotate_pages(90), "Ctrl+Shift+R")
        layout.addWidget(self.rotate_left_button)
        layout.addWidget(self.rotate_right_button)
        layout.addStretch(1)

        self.annotate_button = self._button("pen-line", self.tr("Barra de anotación"), self.toggle_annotation_bar, "Ctrl+E", True)
        self.search_button = self._button("search", self.tr("Buscar"), self.find, "Ctrl+F")
        self.night_button = self._button("moon", self.tr("Modo noche"), lambda: self.set_night_mode(not self.viewer.night_mode), "Ctrl+M", True)
        self.fullscreen_button = self._button("maximize", self.tr("Pantalla completa"), self.fullscreenRequested.emit, "F11")
        for button in (self.annotate_button, self.search_button, self.night_button, self.fullscreen_button):
            layout.addWidget(button)
        layout.addSpacing(4)
        layout.addWidget(separator(True))
        layout.addSpacing(4)

        self.tools_button = IconButton("layout-grid", self.tr("Herramientas"), self, text=self.tr("Herramientas"))
        self.tools_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.tools_menu = QMenu(self)
        self.tools_button.setMenu(self.tools_menu)
        layout.addWidget(self.tools_button)

        self.more_button = IconButton("ellipsis", self.tr("Más opciones"), self)
        self.more_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.more_menu = QMenu(self)
        self.more_button.setMenu(self.more_menu)
        self._fill_more_menu()
        layout.addWidget(self.more_button)
        return bar

    def _fill_more_menu(self) -> None:
        menu = self.more_menu
        menu.addAction(icons.icon("save"), self.tr("Guardar como..."), QKeySequence("Ctrl+Shift+S"), self.save_as)
        menu.addAction(icons.icon("printer"), self.tr("Imprimir..."), QKeySequence("Ctrl+P"), self.print_document)
        menu.addSeparator()
        menu.addAction(icons.icon("copy"), self.tr("Copiar texto seleccionado"), QKeySequence("Ctrl+C"), self.copy)
        menu.addAction(icons.icon("text-select"), self.tr("Seleccionar texto de la página"), QKeySequence("Ctrl+A"),
                       lambda: self.viewer.select_page_text())
        menu.addSeparator()
        menu.addAction(icons.icon("info"), self.tr("Propiedades del documento"), QKeySequence("Ctrl+D"), self.show_properties)

    def set_tools_menu(self, entries: list[tuple[str, str, str]]) -> None:
        """Entradas (id, icono, título) del menú Herramientas."""
        self.tools_menu.clear()
        for tool_id, icon_name, title in entries:
            if tool_id == "-":
                self.tools_menu.addSeparator()
                continue
            action = self.tools_menu.addAction(icons.icon(icon_name), title)
            action.triggered.connect(lambda _=False, t=tool_id: self.request_tool(t))

    def _build_sidebar(self) -> QFrame:
        frame = QFrame(self)
        frame.setObjectName("Sidebar")
        frame.setMinimumWidth(0)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 10, 8, 6)
        layout.setSpacing(8)
        self.sidebar_switch = SegmentedControl([("pages", self.tr("Páginas")), ("outline", self.tr("Índice"))], frame)
        layout.addWidget(self.sidebar_switch)
        self.sidebar_stack = QStackedWidget(frame)
        self.thumb_model = ThumbnailModel(self.document, self.renderer, self)
        self.thumbnails = ThumbnailView(frame)
        self.thumbnails.setModel(self.thumb_model)
        self.thumbnails.pageActivated.connect(lambda p: self.viewer.go_to_page(p))
        self.thumbnails.contextMenuAt.connect(self._thumbnail_menu)
        self.thumb_model.movePages.connect(self.move_pages)
        self.thumb_model.insertFiles.connect(self.insert_files)
        self.outline = OutlinePanel(self.document, frame)
        self.outline.pageRequested.connect(lambda p: self.viewer.jump_to_page(p))
        self.sidebar_stack.addWidget(self.thumbnails)
        self.sidebar_stack.addWidget(self.outline)
        layout.addWidget(self.sidebar_stack, 1)
        self.add_pdf_button = Button(self.tr("Añadir PDF"), "", "file-plus", frame)
        self.add_pdf_button.setToolTip(self.tr(
            "Añade las páginas de otro PDF o de imágenes al final. "
            "También puedes soltar archivos sobre las miniaturas."))
        self.add_pdf_button.clicked.connect(self.add_document_pages)
        layout.addWidget(self.add_pdf_button)
        self.sidebar_hint = QLabel(self.tr("Mantén pulsada una página y arrástrala para moverla"), frame)
        self.sidebar_hint.setProperty("role", "caption")
        self.sidebar_hint.setWordWrap(True)
        self.sidebar_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.sidebar_hint)
        self.sidebar_switch.changed.connect(self._sidebar_view_changed)
        return frame

    def _sidebar_view_changed(self, key: str) -> None:
        pages = key == "pages"
        self.sidebar_stack.setCurrentWidget(self.thumbnails if pages else self.outline)
        self.add_pdf_button.setVisible(pages)
        self.sidebar_hint.setVisible(pages)

    def _build_organizer(self) -> QWidget:
        panel = QWidget(self)
        panel.setObjectName("OrganizerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        bar = QFrame(panel)
        bar.setObjectName("DocToolbar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(20, 10, 16, 10)
        bar_layout.setSpacing(6)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title = QLabel(self.tr("Organizar páginas"))
        title.setProperty("role", "cardtitle")
        hint = QLabel(self.tr("Arrastra las páginas para reordenarlas. Ctrl+rueda cambia el tamaño. "
                              "Doble clic abre la página."))
        hint.setProperty("role", "caption")
        texts.addWidget(title)
        texts.addWidget(hint)
        bar_layout.addLayout(texts, 1)
        for icon_name, text, slot in (
            ("file-plus", self.tr("Añadir PDF"), self.add_document_pages),
            ("file-plus-2", self.tr("Página en blanco"), lambda: self.insert_blank(True)),
        ):
            button = Button(text, "ghost", icon_name, bar)
            button.clicked.connect(slot)
            bar_layout.addWidget(button)
        bar_layout.addWidget(separator(True))
        for icon_name, tip, slot in (
            ("rotate-ccw", self.tr("Girar a la izquierda"), lambda: self.rotate_pages(-90)),
            ("rotate-cw", self.tr("Girar a la derecha"), lambda: self.rotate_pages(90)),
            ("copy", self.tr("Duplicar"), self.duplicate_pages),
            ("file-output", self.tr("Extraer a un PDF nuevo"), self.extract_pages),
            ("trash-2", self.tr("Eliminar"), self.delete_pages),
        ):
            button = IconButton(icon_name, tip, bar)
            button.clicked.connect(slot)
            bar_layout.addWidget(button)
        bar_layout.addWidget(separator(True))
        done = Button(self.tr("Volver al documento"), "primary", "check", bar)
        done.clicked.connect(lambda: self.set_organize_mode(False))
        bar_layout.addWidget(done)
        layout.addWidget(bar)
        self.organizer = OrganizerView(panel)
        self.organizer.setModel(self.thumb_model)
        self.organizer.setObjectName("Organizer")
        self.organizer.rotateRequested.connect(lambda pages, angle: self.rotate_pages(angle, pages))
        self.organizer.deleteRequested.connect(lambda pages: self.delete_pages(pages))
        self.organizer.openPage.connect(self._open_from_organizer)
        self.organizer.contextMenuAt.connect(lambda pos: self._thumbnail_menu(pos, self.organizer))
        layout.addWidget(self.organizer, 1)
        return panel

    def toggle_organize(self) -> None:
        self.set_organize_mode(not self._organizing)

    def set_organize_mode(self, on: bool) -> None:
        if on == self._organizing:
            self.organize_button.setChecked(on)
            return
        self._organizing = on
        self.organize_button.setChecked(on)
        if on:
            if self.viewer.tool is not None:
                self.viewer.tool.cancel()
            if self.search_bar.isVisible():
                self.search_bar.close_bar()
            self._sidebar_before_organize = self.splitter.sizes()[0] > 20
            self.organizer.select_pages(self.thumbnails.selected_pages() or [self.viewer.current_page])
            self.view_stack.setCurrentWidget(self.organizer_panel)
            self._apply_sidebar_width(0)
            self.organizer.setFocus()
        else:
            self.view_stack.setCurrentWidget(self.area)
            if self._sidebar_before_organize:
                self._apply_sidebar_width(self._sidebar_width)
            self.viewer.setFocus()
        for widget in (self.zoom_out_button, self.zoom_box, self.zoom_in_button, self.fit_width_button,
                       self.fit_page_button, self.annotate_button, self.search_button, self.sidebar_button):
            widget.setEnabled(not on)

    def _open_from_organizer(self, page: int) -> None:
        self.set_organize_mode(False)
        self.viewer.go_to_page(page, smooth=False)

    def page_view(self) -> ThumbnailView:
        """Vista de páginas activa: la cuadrícula de organizar o el panel lateral."""
        return self.organizer if self._organizing else self.thumbnails

    def _install_shortcuts(self) -> None:
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        for name, key in TOOL_SHORTCUTS.items():
            shortcut = QShortcut(QKeySequence(key), self, context=context)
            shortcut.activated.connect(lambda n=name: self.set_tool(n))
        for view in (self.thumbnails, self.organizer):
            delete = QShortcut(QKeySequence(QKeySequence.StandardKey.Delete), view, context=Qt.ShortcutContext.WidgetShortcut)
            delete.activated.connect(self.delete_pages)
        organize = QShortcut(QKeySequence("Ctrl+G"), self, context=context)
        organize.activated.connect(self.toggle_organize)
        for key, slot in (
            ("Ctrl+Shift+L", lambda: self.rotate_pages(-90)),
            ("Ctrl+Shift+R", lambda: self.rotate_pages(90)),
            ("Ctrl+1", lambda: self.viewer.set_fit_mode("width")),
            ("Ctrl+2", lambda: self.viewer.set_fit_mode("page")),
            ("Ctrl+0", lambda: self.viewer.set_zoom(1.0)),
            ("Ctrl+M", lambda: self.set_night_mode(not self.viewer.night_mode)),
            ("Ctrl+E", self.toggle_annotation_bar),
            ("Ctrl+C", self.copy),
            ("Ctrl+A", self._select_all),
            ("Ctrl+D", self.show_properties),
            ("F4", self.toggle_sidebar),
            ("Escape", self._escape),
            ("Right", self.viewer.next_page),
            ("Left", self.viewer.previous_page),
            ("Alt+Left", self.viewer.go_back),
            ("Alt+Right", self.viewer.go_forward),
        ):
            shortcut = QShortcut(QKeySequence(key), self, context=context)
            shortcut.activated.connect(slot)

    def _initial_layout(self) -> None:
        visible = settings().sidebar_visible
        self.sidebar_button.setChecked(visible)
        self._apply_sidebar_width(self._sidebar_width if visible else 0)
        self._update_top_inset()
        self.area.layout_overlays()
        saved = settings().reading_position(self.document.path) if self.document.path else None
        if saved and 0 <= int(saved.get("page", 0)) < self.document.page_count:
            fit = str(saved.get("fit", "auto"))
            if fit:
                self.viewer.set_fit_mode(fit)
            else:
                self.viewer.set_zoom(float(saved.get("zoom", 1.0)))
            self.viewer.go_to_page(int(saved["page"]), smooth=False)
        else:
            self.viewer.set_fit_mode("auto")
        self.viewer.setFocus()

    def remember_position(self) -> None:
        if self.document.path:
            settings().set_reading_position(
                self.document.path, self.viewer.current_page, self.viewer.zoom, self.viewer.fit_mode
            )

    # ======================================================================
    # Estado
    # ======================================================================
    def title(self) -> str:
        return self.document.display_name

    def _update_actions(self) -> None:
        undo, redo = self.document.can_undo(), self.document.can_redo()
        self.undo_button.setEnabled(undo)
        self.redo_button.setEnabled(redo)
        label = self.document.history.undo_label()
        self.undo_button.setToolTip(
            self.tr("Deshacer {0} ({1})").format(label.lower(), shortcut_text("Ctrl+Z")) if label
            else self.tr("Deshacer ({0})").format(shortcut_text("Ctrl+Z"))
        )
        redo_label = self.document.history.redo_label()
        self.redo_button.setToolTip(
            self.tr("Rehacer {0} ({1})").format(redo_label.lower(), shortcut_text("Ctrl+Y")) if redo_label
            else self.tr("Rehacer ({0})").format(shortcut_text("Ctrl+Y"))
        )

    def _on_document_changed(self, pages) -> None:
        self.page_total.setText(self.tr("de {0}").format(self.document.page_count))
        self._on_page(self.viewer.current_page)

    def _on_zoom(self, zoom: float) -> None:
        self.zoom_box.lineEdit().setText(f"{round(zoom * 100)} %")

    def _on_fit_mode(self, mode: str) -> None:
        self.fit_width_button.setChecked(mode == "width")
        self.fit_page_button.setChecked(mode == "page")

    def _on_page(self, page: int) -> None:
        self.page_edit.setText(str(page + 1))
        self.page_total.setText(self.tr("de {0}").format(self.document.page_count))
        self.thumbnails.set_current_page(page)

    def _jump_to_page(self) -> None:
        try:
            number = int(self.page_edit.text().strip())
        except ValueError:
            self._on_page(self.viewer.current_page)
            return
        self.viewer.jump_to_page(number - 1)
        self.viewer.setFocus()

    def _zoom_from_text(self) -> None:
        text = self.zoom_box.lineEdit().text().replace("%", "").replace(",", ".").strip()
        try:
            value = float(text)
        except ValueError:
            self._on_zoom(self.viewer.zoom)
            return
        self.viewer.set_zoom(value / 100)
        self.viewer.setFocus()

    # ======================================================================
    # Panel lateral, barras y modos
    # ======================================================================
    def toggle_sidebar(self) -> None:
        visible = self.splitter.sizes()[0] < 20
        settings().sidebar_visible = visible
        self.sidebar_button.setChecked(visible)
        start = self.splitter.sizes()[0]
        end = self._sidebar_width if visible else 0
        self._sidebar_anim.stop()
        self._sidebar_anim.setStartValue(start)
        self._sidebar_anim.setEndValue(end)
        self._sidebar_anim.start()

    def _apply_sidebar_width(self, width) -> None:
        total = sum(self.splitter.sizes()) or self.width()
        self.splitter.setSizes([int(width), max(0, total - int(width))])

    def _sidebar_resized(self, pos: int, index: int) -> None:
        width = self.splitter.sizes()[0]
        if width >= 160:
            self._sidebar_width = width
            settings().sidebar_width = width
        self.sidebar_button.setChecked(width > 20)

    def set_sidebar_visible(self, visible: bool) -> None:
        self.toolbar.setVisible(visible)
        self._apply_sidebar_width(self._sidebar_width if visible and settings().sidebar_visible else 0)

    def toggle_annotation_bar(self) -> None:
        visible = not self.annotation_bar.isVisible()
        settings().annotation_bar_visible = visible
        self.annotation_bar.setVisible(visible)
        self.annotate_button.setChecked(visible)
        if not visible:
            self.set_tool("select")
        self._update_top_inset()
        self.area.layout_overlays(animate=visible)

    def _update_top_inset(self) -> None:
        self.viewer.set_top_inset(56 if self.annotation_bar.isVisible() else 0)

    def set_night_mode(self, enabled: bool) -> None:
        settings().night_mode = enabled
        self.viewer.set_night_mode(enabled)
        self.thumb_model.set_night(enabled)
        self.thumbnails.night = enabled
        self.night_button.setChecked(enabled)

    def find(self) -> None:
        text = self.viewer.selected_text().strip()
        self.search_bar.open_bar(text if text and "\n" not in text else "")
        self.area.layout_overlays()

    def _select_all(self) -> None:
        """Ctrl+A: todas las páginas si el foco está en las miniaturas; si no, el texto de la página."""
        if self.thumbnails.hasFocus():
            self.thumbnails.selectAll()
        else:
            self.viewer.select_page_text()

    def _escape(self) -> None:
        if self.window().isFullScreen():
            self.fullscreenRequested.emit()
        elif self.search_bar.isVisible():
            self.search_bar.close_bar()
        elif self._tool_name not in ("select", "hand"):
            self.set_tool("select")
        else:
            self.viewer.clear_selection()

    def set_tool(self, name: str) -> None:
        if name == "signature":
            dialog = SignatureDialog(self)
            if dialog.exec() != SignatureDialog.DialogCode.Accepted or not dialog.strokes():
                self.annotation_bar.set_active(self._tool_name)
                return
            strokes = dialog.strokes()
        else:
            strokes = None
        if name not in ("select", "hand") and not self.annotation_bar.isVisible():
            self.toggle_annotation_bar()
        tool = self._tools.get(name)
        if tool is None:
            tool = create_tool(name, self.viewer)
            if isinstance(tool, SignatureTool):
                tool.finished.connect(lambda: self.set_tool("select"))
            if isinstance(tool, RedactTool):
                tool.finished.connect(lambda: Toast.show_message(
                    self.window(), self.tr("Contenido censurado de forma definitiva"), self.tr("Deshacer"), self.undo))
            self._tools[name] = tool
        if isinstance(tool, SignatureTool) and strokes:
            tool.set_strokes(strokes)
        self._tool_name = name
        self.viewer.set_tool(tool)
        self.annotation_bar.set_active(name)
        self.area.layout_overlays()

    def _style_changed(self, name: str, style) -> None:
        tool = self._tools.get(name)
        if tool is not None and hasattr(tool, "set_style"):
            tool.set_style(style)
        elif name in STYLED_TOOLS:
            from .interaction import save_style

            save_style(name, style)

    # ======================================================================
    # Páginas
    # ======================================================================
    def target_pages(self) -> list[int]:
        selected = self.page_view().selected_pages()
        return selected if selected else [self.viewer.current_page]

    def _edit(self, label: str, operation, pages=None):
        try:
            return self.document.edit(label, operation, pages)
        except BlackPdfError as exc:
            show_error(self, exc)
        except Exception as exc:
            show_error(self, exc, label)
        return None

    def rotate_pages(self, angle: int, pages: list[int] | None = None) -> None:
        pages = pages or self.target_pages()
        self._edit(self.tr("Girar páginas"), lambda doc: core_pages.rotate_pages(doc, pages, angle), pages)

    def delete_pages(self, pages: list[int] | None = None) -> None:
        pages = pages or self.target_pages()
        try:
            self.document.edit(self.tr("Eliminar páginas"), lambda doc: core_pages.delete_pages(doc, pages))
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        count = len(pages)
        text = self.tr("Página eliminada") if count == 1 else self.tr("{0} páginas eliminadas").format(count)
        Toast.show_message(self.window(), text, self.tr("Deshacer"), self.undo)

    def duplicate_pages(self) -> None:
        pages = self.target_pages()
        result = self._edit(self.tr("Duplicar páginas"), lambda doc: core_pages.duplicate_pages(doc, pages))
        if result:
            self.page_view().select_pages(result)

    def move_pages(self, rows: list, target: int) -> None:
        if not rows:
            return
        result = self._edit(self.tr("Mover páginas"), lambda doc: core_pages.move_pages(doc, rows, target))
        if result:
            QTimer.singleShot(0, lambda: self.page_view().select_pages(result))

    def insert_blank(self, after: bool = True) -> None:
        pages = self.target_pages()
        index = (max(pages) + 1) if after else min(pages)
        result = self._edit(self.tr("Insertar página en blanco"), lambda doc: core_pages.insert_blank_page(doc, index))
        if result is not None:
            QTimer.singleShot(0, lambda: (self.page_view().select_pages([result]), self.viewer.go_to_page(result)))

    def add_document_pages(self) -> None:
        """Añade otro PDF (o imágenes) al final del documento."""
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr("Añadir PDF"), settings().last_directory, open_filter())
        if not paths:
            return
        settings().last_directory = paths[0]
        self.insert_files(paths, self.document.page_count)

    def insert_from_file(self, after: bool = True) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr("Insertar páginas de otro archivo"),
                                                settings().last_directory, open_filter())
        if not paths:
            return
        settings().last_directory = paths[0]
        pages = self.target_pages()
        self.insert_files(paths, (max(pages) + 1) if after else min(pages))

    def insert_files(self, paths: list, index: int) -> None:
        sources = []
        for path in paths:
            password = None
            try:
                if path.lower().endswith(".pdf") and needs_password(path):
                    password = ask_password(self, path)
                    if password is None:
                        return
            except BlackPdfError as exc:
                show_error(self, exc)
                return
            sources.append((path, password))

        def operation(doc):
            position = index
            inserted = []
            for path, password in sources:
                added = core_pages.insert_pdf_pages(doc, path, position, password=password)
                inserted.extend(added)
                position += len(added)
            return inserted

        try:
            result = self.document.edit(self.tr("Insertar páginas"), operation)
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        if result:
            count = len(result)
            text = self.tr("1 página añadida") if count == 1 else self.tr("{0} páginas añadidas").format(count)
            Toast.show_message(self.window(), text + ". " + self.tr("Arrastra las miniaturas para colocarlas."),
                               kind="success")
            QTimer.singleShot(0, lambda: (self.page_view().select_pages(result), self.viewer.go_to_page(result[0])))

    def redact_search_results(self, query: str) -> None:
        from ..core.annotations import redact_text

        count = len(self.search_controller.hits)
        if not query or not count:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(self.tr("Censurar"))
        box.setText("<b>" + self.tr("¿Censurar {0} apariciones de «{1}»?").format(count, query) + "</b>")
        box.setInformativeText(self.tr(
            "El texto se eliminará del documento y quedará un recuadro negro en su lugar. "
            "Puedes deshacerlo con Ctrl+Z mientras no cierres el documento."))
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel)
        box.button(QMessageBox.StandardButton.Yes).setText(self.tr("Censurar"))
        box.button(QMessageBox.StandardButton.Cancel).setText(self.tr("Cancelar"))
        if box.exec() != QMessageBox.StandardButton.Yes:
            return
        result = self._edit(self.tr("Censurar"), lambda doc: redact_text(doc, query))
        if result:
            self.search_bar.close_bar()
            Toast.show_message(self.window(), self.tr("{0} apariciones censuradas").format(result),
                               self.tr("Deshacer"), self.undo)

    def extract_pages(self) -> None:
        pages = self.target_pages()
        spec = format_page_ranges(pages)
        self.request_tool("extract", {"pages": spec})

    def request_tool(self, tool_id: str, preset: dict | None = None) -> None:
        options = dict(preset or {})
        selected = self.page_view().selected_pages()
        options.setdefault("pages", format_page_ranges(selected) if len(selected) > 1 else "")
        self.toolRequested.emit(tool_id, options)

    # ======================================================================
    # Menús contextuales
    # ======================================================================
    def _page_actions(self, menu: QMenu) -> None:
        count = len(self.target_pages())
        plural = count > 1
        menu.addAction(icons.icon("rotate-cw"), self.tr("Girar a la derecha"), lambda: self.rotate_pages(90))
        menu.addAction(icons.icon("rotate-ccw"), self.tr("Girar a la izquierda"), lambda: self.rotate_pages(-90))
        menu.addSeparator()
        menu.addAction(icons.icon("copy"), self.tr("Duplicar páginas") if plural else self.tr("Duplicar página"), self.duplicate_pages)
        menu.addAction(icons.icon("file-plus-2"), self.tr("Insertar página en blanco detrás"), lambda: self.insert_blank(True))
        menu.addAction(icons.icon("file-plus-2"), self.tr("Insertar página en blanco delante"), lambda: self.insert_blank(False))
        menu.addAction(icons.icon("file-input"), self.tr("Insertar desde archivo..."), lambda: self.insert_from_file(True))
        menu.addSeparator()
        menu.addAction(icons.icon("file-output"), self.tr("Extraer páginas..."), self.extract_pages)
        menu.addAction(icons.icon("image"), self.tr("Exportar como imagen..."),
                       lambda: self.request_tool("to_images", {"pages": format_page_ranges(self.target_pages())}))
        menu.addSeparator()
        delete = menu.addAction(icons.icon("trash-2", icons.ROLE_DANGER),
                                self.tr("Eliminar páginas") if plural else self.tr("Eliminar página"), self.delete_pages)
        delete.setShortcut(QKeySequence(QKeySequence.StandardKey.Delete))

    def _thumbnail_menu(self, pos: QPoint, view: ThumbnailView | None = None) -> None:
        view = view or self.thumbnails
        index = view.indexAt(pos)
        if index.isValid() and index.row() not in view.selected_pages():
            view.select_pages([index.row()])
        menu = QMenu(self)
        self._page_actions(menu)
        menu.exec(view.viewport().mapToGlobal(pos))

    def _viewer_menu(self, global_pos, hit) -> None:
        menu = QMenu(self)
        page, words = self.viewer.selection()
        if words:
            menu.addAction(icons.icon("copy"), self.tr("Copiar"), QKeySequence("Ctrl+C"), self.copy)
            menu.addAction(icons.icon("highlighter"), self.tr("Resaltar"), lambda: self._markup_selection("highlight"))
            menu.addAction(icons.icon("underline"), self.tr("Subrayar"), lambda: self._markup_selection("underline"))
            menu.addAction(icons.icon("strikethrough"), self.tr("Tachar"), lambda: self._markup_selection("strikeout"))
            menu.addAction(icons.icon("search"), self.tr("Buscar en el documento"), self.find)
            menu.addSeparator()
        if hit is not None:
            menu.addAction(icons.icon("text-select"), self.tr("Seleccionar texto de la página"),
                           lambda: self.viewer.select_page_text(hit.page))
            menu.addSeparator()
            self.thumbnails.select_pages([hit.page])
            self._page_actions(menu)
            menu.addSeparator()
        menu.addAction(icons.icon("move-horizontal"), self.tr("Ajustar al ancho"), lambda: self.viewer.set_fit_mode("width"))
        menu.addAction(icons.icon("scan"), self.tr("Página completa"), lambda: self.viewer.set_fit_mode("page"))
        menu.exec(global_pos)

    def _markup_selection(self, kind: str) -> None:
        from ..core import annotations as core_annots
        from .interaction import load_style

        page, words = self.viewer.selection()
        if not words:
            return
        from ..core.text import line_rects

        rects = line_rects(words)
        style = load_style(kind)
        labels = {"highlight": self.tr("Resaltar"), "underline": self.tr("Subrayar"), "strikeout": self.tr("Tachar")}
        self._edit(labels[kind], lambda doc: core_annots.add_text_markup(doc[page], kind, rects, style.rgb(), style.opacity), [page])
        self.viewer.clear_selection()

    # ======================================================================
    # Archivo
    # ======================================================================
    def copy(self) -> None:
        if self.viewer.copy_selection():
            Toast.show_message(self.window(), self.tr("Texto copiado"), kind="success", duration=1800)

    def undo(self) -> None:
        self.document.undo()

    def redo(self) -> None:
        self.document.redo()

    def save(self) -> bool:
        if not self.document.path:
            return self.save_as()
        return self._save_to(self.document.path)

    def save_as(self) -> bool:
        suggested = self.document.suggested_path
        if not Path(suggested).is_absolute():
            suggested = str(Path(settings().last_directory) / suggested)
        path, _ = QFileDialog.getSaveFileName(self, self.tr("Guardar como"), suggested, pdf_filter())
        if not path:
            return False
        if not path.lower().endswith(".pdf"):
            path += ".pdf"
        settings().last_directory = path
        return self._save_to(path)

    def _save_to(self, path: str) -> bool:
        self.viewer.setCursor(Qt.CursorShape.WaitCursor)
        try:
            self.document.save(path)
        except BlackPdfError as exc:
            show_error(self, exc)
            return False
        except Exception as exc:
            show_error(self, exc, "Guardar")
            return False
        finally:
            self.viewer.unsetCursor()
        settings().add_recent(path)
        Toast.show_message(self.window(), self.tr("Guardado en {0}").format(Path(path).name), kind="success", duration=2200)
        self.titleChanged.emit()
        return True

    def maybe_close(self) -> bool:
        tool = self.viewer.tool
        if tool is not None:
            tool.deactivate()
        if not self.document.is_modified:
            return True
        answer = confirm_discard(self, self.document.display_name)
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    def print_document(self) -> None:
        try:
            print_document(self, self.document, self.viewer.current_page)
        except Exception as exc:
            show_error(self, exc, "Imprimir")

    def show_properties(self) -> None:
        PropertiesDialog(document_info(self.document.doc), self.document.path, self).exec()

    def shutdown(self) -> None:
        self.remember_position()
        self.search_controller.stop()
        if self.viewer.tool is not None:
            self.viewer.tool.cancel()
