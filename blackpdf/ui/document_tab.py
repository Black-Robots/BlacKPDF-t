"""Pestaña de documento: barra de herramientas, panel lateral y visor."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QCursor, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSizePolicy,
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
from . import formatting, icons, tasks, theme
from .annotation_bar import TOOL_SHORTCUTS, AnnotationBar
from .dialogs.common import PropertiesDialog, ask_password, confirm_discard
from .dialogs.signature import SignatureDialog
from .document import PdfDocument
from .errors import show_error
from .interaction import STYLED_TOOLS, RedactTool, SignatureTool, create_tool
from .keys import shortcut_text
from .comments import CommentsPanel
from .outline import OutlinePanel
from .printing import print_document
from .render import PageRenderer
from .search import SearchBar, SearchController
from .split_view import SplitPane
from .settings import settings
from .thumbnails import OrganizerView, ThumbnailModel, ThumbnailView
from .viewer import PdfViewer
from .widgets import (
    SHADOW_TOP,
    AutoStack,
    Button,
    IconButton,
    SectionLabel,
    SegmentedControl,
    SplitButton,
    Toast,
    open_filter,
    pdf_filter,
    separator,
)

ZOOM_PRESETS = (50, 75, 100, 125, 150, 200, 300, 400)




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
            self.annotation_bar.set_compact(self.annotation_bar.full_width() > self.width() - 32)
            self.annotation_bar.place(self.width() - 14, 12, animate)
        if self.search_bar is not None and self.search_bar.isVisible():
            bar = self.search_bar
            bar.adjustSize()
            width = min(max(bar.sizeHint().width(), 408), self.width() - 4)
            bar.resize(width, bar.height())
            top = 12
            if self.annotation_bar is not None and self.annotation_bar.isVisible():
                top = int(self.annotation_bar.geometry().top() + self.annotation_bar.panel_rect().bottom()) + 10
            bar.move(self.width() - width - 10, top - SHADOW_TOP)


class SignatureBanner(QFrame):
    """Barra bajo la barra de herramientas con el estado de las firmas digitales."""

    detailsRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("SignatureBanner")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 8, 10, 8)
        layout.setSpacing(10)
        self.icon = QLabel(self)
        layout.addWidget(self.icon)
        self.text = QLabel(self)
        self.text.setWordWrap(True)
        layout.addWidget(self.text, 1)
        self.details = Button(self.tr("Ver firmas"), "ghost", "file-badge", self)
        self.details.clicked.connect(self.detailsRequested)
        layout.addWidget(self.details)
        self.hide()

    def show_state(self, level: str, icon_name: str, text: str, details: bool = True) -> None:
        from .dialogs.certificate import ROLE_FOR_LEVEL

        self.setProperty("level", level)
        self.style().unpolish(self)
        self.style().polish(self)
        role = ROLE_FOR_LEVEL.get(level, icons.ROLE_MUTED)
        self.icon.setPixmap(icons.pixmap(icon_name, 18, role, self.devicePixelRatioF()))
        self.text.setText(text)
        self.details.setVisible(details)
        self.show()


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
        self._comments_width = settings().comments_width
        self._comments_shown = settings().comments_visible

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)
        self.signature_banner = SignatureBanner(self)
        self.signature_banner.detailsRequested.connect(self.show_signatures)
        root.addWidget(self.signature_banner)
        self._signatures: list = []
        self._signature_task = None

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setChildrenCollapsible(True)
        self.splitter.setHandleWidth(1)
        self.sidebar = self._build_sidebar()
        self.area = ViewerArea(self.viewer)
        self.organizer_panel = self._build_organizer()
        self.view_splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.view_splitter.setHandleWidth(1)
        self.view_splitter.setChildrenCollapsible(False)
        self.view_splitter.addWidget(self.area)
        self.split_pane: SplitPane | None = None
        self.documents_provider = None
        self.view_stack = AutoStack(self)
        self.view_stack.addWidget(self.view_splitter)
        self.view_stack.addWidget(self.organizer_panel)
        self._organizing = False
        self._sidebar_before_organize = True
        self.comments_frame = self._build_comments_panel()
        self.splitter.addWidget(self.sidebar)
        self.splitter.addWidget(self.view_stack)
        self.splitter.addWidget(self.comments_frame)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)
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
        self._comments_anim = QVariantAnimation(self)
        self._comments_anim.setDuration(theme.ANIMATION_MS)
        self._comments_anim.valueChanged.connect(lambda width: self._set_panel_widths(right=width))

        self.viewer.zoomChanged.connect(self._on_zoom)
        self.viewer.currentPageChanged.connect(self._on_page)
        self.viewer.fitModeChanged.connect(self._on_fit_mode)
        self.viewer.contextMenuRequested.connect(self._viewer_menu)
        self.viewer.modelRequested.connect(self.open_model)
        self._model_windows: dict[int, QWidget] = {}
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
        QTimer.singleShot(150, self._check_signatures)
        document.modifiedChanged.connect(self._signed_document_modified)

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
        # El ancho mínimo lo gestiona _adapt_toolbar ocultando grupos: así la ventana puede estrecharse.
        bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(8, 0, 8, 0)
        layout.setSpacing(2)

        self.sidebar_button = self._button("panel-left", self.tr("Panel lateral"), self.toggle_sidebar, "F4", True)
        layout.addWidget(self.sidebar_button)
        self.organize_button = self._button("layout-grid", self.tr("Organizar páginas"), self.toggle_organize, "Ctrl+G", True)
        layout.addWidget(self.organize_button)
        self.split_button = self._button("columns-2", self.tr("Vista dividida"), self.toggle_split, "Ctrl+\\", True)
        layout.addWidget(self.split_button)
        layout.addSpacing(4)
        layout.addWidget(separator(True))
        layout.addSpacing(4)
        self.save_button = SplitButton(
            "save", f"{self.tr('Guardar')} ({shortcut_text('Ctrl+S')})", self.tr("Guardar como..."), bar
        )
        self.save_button.clicked.connect(self.save)
        self.save_button.menu.addAction(icons.icon("save"), self.tr("Guardar como..."), QKeySequence("Ctrl+Shift+S"),
                                        self.save_as)
        self.undo_button = self._button("undo-2", self.tr("Deshacer"), self.undo, "Ctrl+Z")
        self.redo_button = self._button("redo-2", self.tr("Rehacer"), self.redo, "Ctrl+Y")
        layout.addWidget(self.save_button)
        layout.addSpacing(2)
        for button in (self.undo_button, self.redo_button):
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
            self.zoom_box.addItem(formatting.percent(value), value / 100)
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
        self._rotate_separator = separator(True)
        layout.addWidget(self._rotate_separator)
        layout.addSpacing(4)
        self.rotate_left_button = self._button("rotate-ccw", self.tr("Girar a la izquierda"), lambda: self.rotate_pages(-90), "Ctrl+Shift+L")
        self.rotate_right_button = self._button("rotate-cw", self.tr("Girar a la derecha"), lambda: self.rotate_pages(90), "Ctrl+Shift+R")
        layout.addWidget(self.rotate_left_button)
        layout.addWidget(self.rotate_right_button)
        layout.addStretch(1)

        self.annotate_button = self._button("pen-line", self.tr("Barra de anotación"), self.toggle_annotation_bar, "Ctrl+E", True)
        self.comments_button = self._button("message-square", self.tr("Comentarios"), self.toggle_comments, "", True)
        self.search_button = self._button("search", self.tr("Buscar"), self.find, "Ctrl+F")
        self.night_button = self._button("moon", self.tr("Modo noche"), lambda: self.set_night_mode(not self.viewer.night_mode), "Ctrl+M", True)
        self.fullscreen_button = self._button("expand", self.tr("Pantalla completa"), self.fullscreenRequested.emit, "F11")
        for button in (self.annotate_button, self.comments_button, self.search_button, self.night_button,
                       self.fullscreen_button):
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
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(10)
        self.sidebar_switch = SegmentedControl(
            [("pages", ""), ("outline", "")],
            frame,
            icons_map={"pages": "layout-grid", "outline": "list-tree"},
            tooltips={"pages": self.tr("Páginas"), "outline": self.tr("Índice")},
        )
        layout.addWidget(self.sidebar_switch)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 0)
        self.sidebar_title = SectionLabel(self.tr("Páginas"), frame)
        header.addWidget(self.sidebar_title)
        header.addStretch(1)
        self.sidebar_count = QLabel("", frame)
        self.sidebar_count.setProperty("role", "caption")
        header.addWidget(self.sidebar_count)
        layout.addLayout(header)
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
        self._update_sidebar_header()
        return frame

    def _update_sidebar_header(self) -> None:
        key = self.sidebar_switch.value()
        titles = {"pages": self.tr("Páginas"), "outline": self.tr("Índice")}
        self.sidebar_title.setText(titles.get(key, "").upper())
        self.sidebar_count.setText(str(self.document.page_count) if key == "pages" else "")

    def _build_comments_panel(self) -> QFrame:
        """Panel de comentarios a la derecha de las páginas, independiente del panel lateral."""
        frame = QFrame(self)
        frame.setObjectName("CommentsSidebar")
        frame.setMinimumWidth(0)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 10, 10, 8)
        layout.setSpacing(10)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 0, 0)
        header.setSpacing(6)
        header.addWidget(SectionLabel(self.tr("Comentarios").upper(), frame))
        header.addStretch(1)
        self.comments_count = QLabel("", frame)
        self.comments_count.setProperty("role", "caption")
        header.addWidget(self.comments_count)
        hide = IconButton("x", self.tr("Ocultar comentarios"), frame, size=26, icon_size=14)
        hide.clicked.connect(lambda: self.set_comments_visible(False))
        header.addWidget(hide)
        layout.addLayout(header)
        self.comments = CommentsPanel(self.document, lambda: self.viewer.current_page, frame)
        self.comments.commentActivated.connect(self._show_comment)
        self.comments.countChanged.connect(self._comments_count_changed)
        layout.addWidget(self.comments, 1)
        return frame

    def _comments_count_changed(self, count: int) -> None:
        self.comments_count.setText(str(count) if count else "")

    def _show_comment(self, page: int, rect) -> None:
        self.viewer.ensure_visible(page, rect)
        self.viewer.flash(page, rect)

    def show_comments(self, edit_xref: int | None = None) -> None:
        if not self._comments_shown:
            self.set_comments_visible(True)
        if edit_xref is not None:
            self.comments.edit_comment_later(edit_xref)

    def toggle_comments(self) -> None:
        self.set_comments_visible(not self._comments_shown)

    def set_comments_visible(self, visible: bool) -> None:
        self._comments_shown = visible
        settings().comments_visible = visible
        self.comments_button.setChecked(visible)
        self._comments_anim.stop()
        self._comments_anim.setStartValue(self.splitter.sizes()[2])
        self._comments_anim.setEndValue(self._comments_width if visible else 0)
        self._comments_anim.start()

    def _sidebar_view_changed(self, key: str) -> None:
        pages = key == "pages"
        widget = {"pages": self.thumbnails, "outline": self.outline}[key]
        self.sidebar_stack.setCurrentWidget(widget)
        self.add_pdf_button.setVisible(pages)
        self.sidebar_hint.setVisible(pages)
        self._update_sidebar_header()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._adapt_toolbar()

    def _adapt_toolbar(self) -> None:
        """Con poco ancho se ocultan primero los controles menos usados (siguen en menús y atajos)."""
        width = self.width()
        self.tools_button.setToolButtonStyle(
            Qt.ToolButtonStyle.ToolButtonTextBesideIcon if width >= 1180 else Qt.ToolButtonStyle.ToolButtonIconOnly
        )
        self.tools_button.setProperty("variant", "text" if width >= 1180 else "")
        groups = (
            ((self._rotate_separator, self.rotate_left_button, self.rotate_right_button), 1080),
            ((self.fit_width_button, self.fit_page_button), 980),
            ((self.night_button, self.fullscreen_button), 900),
            ((self.page_total,), 820),
            ((self.zoom_box,), 760),
        )
        for widgets, threshold in groups:
            for widget in widgets:
                widget.setVisible(width >= threshold)
        compact = width < 1100
        for button in getattr(self, "_organizer_text_buttons", []):
            button.setText("" if compact else button.property("label"))

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
        hint.setWordWrap(True)
        hint.setMinimumWidth(120)
        texts.addWidget(title)
        texts.addWidget(hint)
        bar_layout.addLayout(texts, 1)
        self._organizer_text_buttons = []
        for icon_name, text, slot in (
            ("file-plus", self.tr("Añadir PDF"), self.add_document_pages),
            ("file-plus-2", self.tr("Página en blanco"), lambda: self.insert_blank(True)),
        ):
            button = Button(text, "ghost", icon_name, bar)
            button.setProperty("label", text)
            button.setToolTip(text)
            button.clicked.connect(slot)
            bar_layout.addWidget(button)
            self._organizer_text_buttons.append(button)
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
            self._set_panel_widths(left=0, right=0)
            self.organizer.setFocus()
        else:
            self.view_stack.setCurrentWidget(self.view_splitter)
            self._set_panel_widths(left=self._sidebar_width if self._sidebar_before_organize else 0,
                                   right=self._comments_width if self._comments_shown else 0)
            self.viewer.setFocus()
        for widget in (self.zoom_out_button, self.zoom_box, self.zoom_in_button, self.fit_width_button,
                       self.fit_page_button, self.annotate_button, self.comments_button, self.search_button,
                       self.sidebar_button, self.split_button):
            widget.setEnabled(not on)

    def _open_from_organizer(self, page: int) -> None:
        self.set_organize_mode(False)
        self.viewer.go_to_page(page, smooth=False)

    # ---------- vista dividida ----------
    def toggle_split(self) -> None:
        self.set_split(self.split_pane is None)

    def set_split(self, on: bool, model: PdfDocument | None = None) -> None:
        if on and self.split_pane is None:
            pane = SplitPane(self.viewer, self.document, self.documents_provider, self.view_splitter)
            pane.closeRequested.connect(lambda: self.set_split(False))
            self.view_splitter.addWidget(pane)
            pane.set_document(model or self.document, page=self.viewer.current_page)
            pane.set_night_mode(self.viewer.night_mode)
            total = max(self.view_splitter.width(), 2)
            self.view_splitter.setSizes([total // 2, total - total // 2])
            self.split_pane = pane
        elif not on and self.split_pane is not None:
            pane = self.split_pane
            self.split_pane = None
            pane.shutdown()
            pane.hide()
            pane.setParent(None)
            pane.deleteLater()
            self.viewer.setFocus()
        elif on and model is not None and self.split_pane is not None:
            self.split_pane.set_document(model)
        self.split_button.setChecked(self.split_pane is not None)

    def document_closing(self, model: PdfDocument) -> None:
        if self.split_pane is not None:
            self.split_pane.document_closing(model)

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
        split = QShortcut(QKeySequence("Ctrl+\\"), self, context=context)
        split.activated.connect(self.toggle_split)
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
        self.comments_button.setChecked(self._comments_shown)
        self._set_panel_widths(left=self._sidebar_width if visible else 0,
                               right=self._comments_width if self._comments_shown else 0)
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

    # ---------- firmas digitales ----------
    def _check_signatures(self) -> None:
        from ..core.signatures import has_signatures

        path = self.document.path
        if not path or not path.lower().endswith(".pdf"):
            return
        try:
            signed = has_signatures(self.document.doc)
        except Exception:
            return
        if not signed:
            return
        self.signature_banner.show_state("", "shield", self.tr("Comprobando las firmas digitales..."), details=False)
        task = tasks.start("blackpdf.core.signatures:list_signatures", path)
        task.signals.succeeded.connect(self._signatures_checked)
        task.signals.failed.connect(lambda _: self.signature_banner.hide())
        task.signals.crashed.connect(lambda _: self.signature_banner.hide())
        self._signature_task = task

    def _signatures_checked(self, statuses) -> None:
        from .dialogs.certificate import status_text

        self._signatures = list(statuses or [])
        if not self._signatures:
            self.signature_banner.hide()
            return
        if self.document.is_modified:
            self._signed_document_modified(True)
            return
        levels = [s.level for s in self._signatures]
        level = "invalid" if "invalid" in levels else ("warning" if "warning" in levels else "valid")
        if len(self._signatures) == 1:
            icon_name, text = status_text(self._signatures[0])
        else:
            count = len(self._signatures)
            icon_name = {"valid": "shield-check", "warning": "shield-alert", "invalid": "shield-x"}[level]
            text = {
                "valid": self.tr("{0} firmas digitales. Todas son válidas y el documento no se ha modificado."),
                "warning": self.tr("{0} firmas digitales. Revisa los detalles: alguna requiere atención."),
                "invalid": self.tr("{0} firmas digitales. Al menos una no es válida."),
            }[level].format(count)
        self.signature_banner.show_state(level, icon_name, text)

    def _signed_document_modified(self, modified: bool) -> None:
        if not modified or not self._signatures:
            return
        self.signature_banner.show_state("warning", "shield-alert", self.tr(
            "Has modificado un documento firmado: al guardar, sus firmas dejarán de ser válidas. "
            "Si guardas con otro nombre, el archivo original las conserva intactas."))

    def show_signatures(self) -> None:
        from .dialogs.certificate import SignaturesDialog

        if not self._signatures:
            return
        dialog = SignaturesDialog(self._signatures, self)
        dialog.goToSignature.connect(lambda page, rect: (self.viewer.ensure_visible(page, rect), self.viewer.flash(page, rect)))
        dialog.exec()

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
        self._update_sidebar_header()

    def _on_zoom(self, zoom: float) -> None:
        self.zoom_box.lineEdit().setText(formatting.percent(zoom * 100))

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

    def _animate_sidebar(self, start: int, end: int) -> None:
        self._sidebar_anim.stop()
        self._sidebar_anim.setStartValue(start)
        self._sidebar_anim.setEndValue(end)
        self._sidebar_anim.start()

    def _apply_sidebar_width(self, width) -> None:
        self._set_panel_widths(left=width)

    def _set_panel_widths(self, left=None, right=None) -> None:
        """Ancho del panel lateral (izquierda) y del de comentarios (derecha); el visor ocupa el resto."""
        sizes = self.splitter.sizes()
        total = sum(sizes) or self.width()
        left = sizes[0] if left is None else int(left)
        right = sizes[2] if right is None else int(right)
        self.splitter.setSizes([left, max(0, total - left - right), right])

    def _sidebar_resized(self, pos: int, index: int) -> None:
        left, _, right = self.splitter.sizes()
        if left >= 160:
            self._sidebar_width = left
            settings().sidebar_width = left
        self.sidebar_button.setChecked(left > 20)
        if index == 2:
            if right >= 240:
                self._comments_width = right
                settings().comments_width = right
            if (right > 20) != self._comments_shown:
                self._comments_shown = right > 20
                settings().comments_visible = self._comments_shown
            self.comments_button.setChecked(self._comments_shown)

    def set_sidebar_visible(self, visible: bool) -> None:
        self.toolbar.setVisible(visible)
        self._set_panel_widths(left=self._sidebar_width if visible and settings().sidebar_visible else 0,
                               right=self._comments_width if visible and self._comments_shown else 0)

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
        if getattr(self, "split_pane", None) is not None:
            self.split_pane.set_night_mode(enabled)

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
            menu.addAction(icons.icon("message-square-plus"), self.tr("Resaltar y comentar"),
                           lambda: self._markup_selection("highlight", comment=True))
            menu.addAction(icons.icon("underline"), self.tr("Subrayar"), lambda: self._markup_selection("underline"))
            menu.addAction(icons.icon("strikethrough"), self.tr("Tachar"), lambda: self._markup_selection("strikeout"))
            menu.addAction(icons.icon("search"), self.tr("Buscar en el documento"), self.find)
            menu.addSeparator()
        model = self.viewer.model_at(hit)
        if model is not None:
            menu.addAction(icons.icon("box"), self.tr("Ver el modelo 3D"), lambda: self.open_model(model))
            menu.addSeparator()
        if hit is not None:
            from ..core.annotations import annotation_at

            tolerance = 4 / max(self.viewer.scale, 0.1)
            xref = annotation_at(self.document.doc[hit.page], (hit.point.x(), hit.point.y()), tolerance)
            if xref is not None:
                menu.addAction(icons.icon("message-square-text"), self.tr("Comentar esta anotación"),
                               lambda: self.show_comments(edit_xref=xref))
            menu.addAction(icons.icon("message-square-plus"), self.tr("Añadir comentario aquí"),
                           lambda: self._comment_at(hit.page, hit.point))
            menu.addAction(icons.icon("text-select"), self.tr("Seleccionar texto de la página"),
                           lambda: self.viewer.select_page_text(hit.page))
            menu.addSeparator()
            self.page_view().select_pages([hit.page])
            self._page_actions(menu)
            menu.addSeparator()
        menu.addAction(icons.icon("move-horizontal"), self.tr("Ajustar al ancho"), lambda: self.viewer.set_fit_mode("width"))
        menu.addAction(icons.icon("scan"), self.tr("Página completa"), lambda: self.viewer.set_fit_mode("page"))
        menu.exec(global_pos)

    def _comment_at(self, page: int, point) -> None:
        from PySide6.QtWidgets import QInputDialog

        from ..core.comments import add_page_comment

        text, ok = QInputDialog.getMultiLineText(self, self.tr("Nuevo comentario"),
                                                 self.tr("Comentario en la página {0}:").format(page + 1))
        if not ok or not text.strip():
            return
        position = (point.x(), point.y())
        result = self._edit(self.tr("Añadir comentario"),
                            lambda doc: add_page_comment(doc[page], text, settings().author_name, position), [page])
        if result:
            self.show_comments()

    def _markup_selection(self, kind: str, comment: bool = False) -> None:
        from ..core import annotations as core_annots
        from ..core.text import line_rects
        from .interaction import load_style

        page, words = self.viewer.selection()
        if not words:
            return
        rects = line_rects(words)
        style = load_style(kind)
        labels = {"highlight": self.tr("Resaltar"), "underline": self.tr("Subrayar"), "strikeout": self.tr("Tachar")}
        annot = self._edit(
            labels[kind],
            lambda doc: core_annots.add_text_markup(doc[page], kind, rects, style.rgb(), style.opacity).xref,
            [page],
        )
        self.viewer.clear_selection()
        if comment and annot:
            self.show_comments(edit_xref=annot)

    # ======================================================================
    # Archivo
    # ======================================================================
    def copy(self) -> None:
        if self.split_pane is not None and self.split_pane.viewer is not None and self.split_pane.viewer.hasFocus():
            if self.split_pane.viewer.copy_selection():
                Toast.show_message(self.window(), self.tr("Texto copiado"), kind="success", duration=1800)
            return
        if self.viewer.copy_selection():
            Toast.show_message(self.window(), self.tr("Texto copiado"), kind="success", duration=1800)

    def undo(self) -> None:
        self.document.undo()

    def redo(self) -> None:
        self.document.redo()

    def save(self) -> bool:
        if not self.document.path:
            return self.save_as()
        if not self.document.is_modified:
            # Nunca se reescribe un archivo sin cambios (rompería, por ejemplo, sus firmas digitales).
            Toast.show_message(self.window(), self.tr("No hay cambios que guardar"), duration=1800)
            return True
        if self._signatures:
            answer = QMessageBox.question(
                self, self.tr("Documento firmado"),
                self.tr("Este documento tiene firmas digitales. Si lo guardas con cambios, sus firmas dejarán de "
                        "ser válidas.\n\n¿Guardar de todos modos? Con «Guardar como» el original conserva las firmas."),
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
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
        if self._signatures:
            self._signatures = []
            self.signature_banner.hide()
            QTimer.singleShot(0, self._check_signatures)
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

    # ======================================================================
    # Modelos 3D
    # ======================================================================
    def models_3d(self) -> list:
        from ..core import model3d

        try:
            return model3d.find_models(self.document.doc)
        except Exception:
            return []

    def open_model(self, item) -> None:
        """Abre el modelo 3D en su propia ventana (o la trae al frente)."""
        from ..core import model3d
        from .model3d_window import Model3DWindow, show_window

        window = self._model_windows.get(item.xref)
        if window is not None:
            show_window(window)
            return
        try:
            embedded = model3d.extract_from_pdf(self.document.doc, item.xref)
        except BlackPdfError as exc:
            show_error(self, exc)
            return
        name = self.document.short_name
        window = Model3DWindow(name, self.tr("Página {0}").format(item.page + 1), self.window())
        self._model_windows[item.xref] = window
        window.closed.connect(lambda xref=item.xref: self._model_windows.pop(xref, None))
        window.load("blackpdf.core.model3d:load_embedded", embedded, name)
        show_window(window)

    def show_models(self) -> None:
        """Abre el único modelo 3D o deja elegir entre varios."""
        found = self.models_3d()
        if not found:
            Toast.show_message(self.window(), self.tr("Este documento no contiene modelos 3D"))
            return
        if len(found) == 1:
            self.open_model(found[0])
            return
        menu = QMenu(self)
        for item in found:
            menu.addAction(icons.icon("box"), self.tr("Página {0} · {1}").format(item.page + 1, item.format),
                           lambda i=item: self.open_model(i))
        menu.exec(QCursor.pos())

    def show_properties(self) -> None:
        PropertiesDialog(document_info(self.document.doc), self.document.path, self).exec()

    def shutdown(self) -> None:
        self.remember_position()
        if self.split_pane is not None:
            self.split_pane.shutdown()
        self.search_controller.stop()
        if self.viewer.tool is not None:
            self.viewer.tool.cancel()
