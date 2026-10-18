"""Ventana principal: cabecera con pestañas, inicio y documentos abiertos."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..core.document import OPENABLE_EXTENSIONS
from ..core.model3d import MODEL_EXTENSIONS
from ..core.errors import BlackPdfError, PasswordRequiredError, WrongPasswordError
from . import icons, platform, tasks, theme, tools
from .command_palette import Command, CommandPalette
from .dialogs.about import AboutDialog, ShortcutsDialog
from .dialogs.base import SourceContext
from .dialogs.common import ask_password
from .dialogs.forms import FormFillDialog
from .document import PdfDocument
from .document_tab import DocumentTab, shortcut_text
from .errors import show_error
from .home import HomePage
from .settings import settings
from .widgets import (
    IconButton,
    SearchPill,
    TabCloseButton,
    Toast,
    dropped_paths,
    format_icon,
    open_any_filter,
    open_filter,
)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self.setAcceptDrops(True)
        self.setMinimumSize(QSize(900, 600))
        self._tabs: list[QWidget] = []
        self._actions: list[QAction] = []
        self._fullscreen_restore = False
        self._model_windows: dict[str, QWidget] = {}

        central = QWidget(self)
        central.setObjectName("Window")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.header = self._build_header()
        root.addWidget(self.header)
        self.stack = QStackedWidget(central)
        root.addWidget(self.stack, 1)
        self.setCentralWidget(central)

        self.home = HomePage(self)
        self.home.openRequested.connect(self.open_dialog)
        self.home.filesDropped.connect(self.open_files)
        self.home.recentActivated.connect(lambda path: self.open_files([path]))
        self.home.toolActivated.connect(lambda tool_id: self.launch_tool(tool_id, from_home=True))
        self._add_page(self.home, icons.icon("house"), self.tr("Inicio"), closable=False)
        self.tab_bar.setTabToolTip(0, self.tr("Inicio"))

        self.palette_overlay = CommandPalette(central)
        self._install_actions()
        theme.manager().changed.connect(self._theme_changed)

        geometry = settings().window_geometry()
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1320, 860)
        self._update_window_title()
        QTimer.singleShot(0, self._theme_changed)

    # ======================================================================
    # Cabecera
    # ======================================================================
    def _build_header(self) -> QFrame:
        header = QFrame(self)
        header.setObjectName("Header")
        header.setFixedHeight(52)
        layout = QHBoxLayout(header)
        layout.setContentsMargins(18, 0, 10, 0)
        layout.setSpacing(6)
        brand = QLabel(APP_NAME)
        brand.setProperty("role", "brand")
        layout.addWidget(brand)
        layout.addSpacing(14)

        self.tab_bar = QTabBar(header)
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setTabsClosable(True)
        self.tab_bar.setMovable(True)
        self.tab_bar.tabMoved.connect(self._tab_moved)
        self._fixing_home = False
        self.tab_bar.setExpanding(False)
        self.tab_bar.setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.setIconSize(QSize(16, 16))
        self.tab_bar.setDrawBase(False)
        self.tab_bar.currentChanged.connect(self._tab_changed)
        self.tab_bar.tabCloseRequested.connect(self.close_tab)
        self.tab_bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_bar.customContextMenuRequested.connect(self._tab_menu)
        layout.addWidget(self.tab_bar, 1, Qt.AlignmentFlag.AlignVCenter)

        self.palette_button = SearchPill(self.tr("Buscar herramientas y acciones"), shortcut_text("Ctrl+K"), header)
        self.palette_button.setToolTip(self.tr("Paleta de comandos ({0})").format(shortcut_text("Ctrl+K")))
        self.palette_button.clicked.connect(self.show_palette)
        layout.addWidget(self.palette_button)
        layout.addSpacing(6)
        open_button = IconButton("folder-open", self.tr("Abrir ({0})").format(shortcut_text("Ctrl+O")), header)
        open_button.clicked.connect(self.open_dialog)
        layout.addWidget(open_button)
        self.theme_button = IconButton("sun", self.tr("Cambiar tema"), header)
        self.theme_button.clicked.connect(self.toggle_theme)
        layout.addWidget(self.theme_button)
        menu_button = IconButton("menu", self.tr("Menú"), header)
        menu_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.app_menu = QMenu(self)
        self.app_menu.aboutToShow.connect(self._fill_app_menu)
        menu_button.setMenu(self.app_menu)
        layout.addWidget(menu_button)
        return header

    def _fill_app_menu(self) -> None:
        menu = self.app_menu
        menu.clear()
        menu.addAction(icons.icon("folder-open"), self.tr("Abrir..."), QKeySequence("Ctrl+O"), self.open_dialog)
        recent_menu = menu.addMenu(icons.icon("clock"), self.tr("Abrir reciente"))
        recents = [p for p in settings().recent_files() if Path(p).exists()]
        for path in recents:
            recent_menu.addAction(icons.icon("file-text"), Path(path).name, lambda p=path: self.open_files([p]))
        if not recents:
            empty = recent_menu.addAction(self.tr("No hay archivos recientes"))
            empty.setEnabled(False)
        else:
            recent_menu.addSeparator()
            recent_menu.addAction(self.tr("Borrar lista"), self._clear_recents)
        menu.addSeparator()
        tools_menu = menu.addMenu(icons.icon("layout-grid"), self.tr("Herramientas"))
        for key, name in tools.categories():
            tools_menu.addSection(name)
            for item in tools.tool_specs():
                if item.category == key:
                    tools_menu.addAction(icons.icon(item.icon), item.title, lambda t=item.id: self.launch_tool(t))
        theme_menu = menu.addMenu(icons.icon("palette"), self.tr("Tema"))
        for mode, name in (("dark", self.tr("Oscuro")), ("light", self.tr("Claro")), ("system", self.tr("Según el sistema"))):
            action = theme_menu.addAction(name, lambda m=mode: self.set_theme(m))
            action.setCheckable(True)
            action.setChecked(theme.manager().mode == mode)
        menu.addSeparator()
        menu.addAction(icons.icon("keyboard"), self.tr("Atajos de teclado"), lambda: ShortcutsDialog(self).exec())
        menu.addAction(icons.icon("info"), self.tr("Acerca de BlackPDF"), lambda: AboutDialog(self).exec())
        menu.addSeparator()
        menu.addAction(icons.icon("x"), self.tr("Salir"), QKeySequence("Ctrl+Q"), self.close)

    def _clear_recents(self) -> None:
        settings().clear_recents()
        self.home.refresh_recents()

    # ======================================================================
    # Pestañas
    # ======================================================================
    def _add_page(self, widget: QWidget, icon, title: str, closable: bool = True) -> int:
        self._tabs.append(widget)
        self.stack.addWidget(widget)
        index = self.tab_bar.addTab(icon, title)
        if not closable:
            for side in (QTabBar.ButtonPosition.RightSide, QTabBar.ButtonPosition.LeftSide):
                self.tab_bar.setTabButton(index, side, None)
        else:
            close = TabCloseButton(self.tab_bar)
            close.setToolTip(self.tr("Cerrar ({0})").format(shortcut_text("Ctrl+W")))
            close.clicked.connect(lambda _=False, w=widget: self.close_tab(self._tabs.index(w)))
            self.tab_bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        return index

    def _open_documents(self) -> list:
        return [(tab.document.display_name, tab.document) for tab in self.document_tabs()]

    def _tab_moved(self, origin: int, target: int) -> None:
        widget = self._tabs.pop(origin)
        self._tabs.insert(target, widget)
        if self._tabs[0] is not self.home and not self._fixing_home:
            # La pestaña de inicio siempre va la primera.
            self._fixing_home = True
            self.tab_bar.moveTab(self._tabs.index(self.home), 0)
            self._fixing_home = False

    def current_tab(self) -> DocumentTab | None:
        index = self.tab_bar.currentIndex()
        if 0 <= index < len(self._tabs):
            widget = self._tabs[index]
            return widget if isinstance(widget, DocumentTab) else None
        return None

    def document_tabs(self) -> list[DocumentTab]:
        return [w for w in self._tabs if isinstance(w, DocumentTab)]

    def _tab_changed(self, index: int) -> None:
        if 0 <= index < len(self._tabs):
            self.stack.setCurrentWidget(self._tabs[index])
            widget = self._tabs[index]
            if isinstance(widget, DocumentTab):
                QTimer.singleShot(0, widget.viewer.setFocus)
            else:
                self.home.refresh_recents()
        self._update_window_title()
        self._update_actions()

    def _tab_title(self, tab: DocumentTab) -> str:
        return tab.document.short_name

    def _refresh_tab(self, tab: DocumentTab) -> None:
        if tab in self._tabs:
            index = self._tabs.index(tab)
            self.tab_bar.setTabText(index, self._tab_title(tab))
            self.tab_bar.setTabIcon(index, format_icon(tab.document.format_suffix))
            self.tab_bar.setTabToolTip(index, tab.document.path or tab.title())
            button = self.tab_bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if isinstance(button, TabCloseButton):
                button.set_modified(tab.document.is_modified)
        self._update_window_title()

    def _update_window_title(self) -> None:
        tab = self.current_tab()
        if tab is None:
            self.setWindowTitle(APP_NAME)
            self.setWindowModified(False)
        else:
            self.setWindowTitle(f"{tab.title()}[*] - {APP_NAME}")
            self.setWindowModified(tab.document.is_modified)

    def close_tab(self, index: int) -> bool:
        if index <= 0 or index >= len(self._tabs):
            return False
        tab = self._tabs[index]
        if isinstance(tab, DocumentTab):
            if not tab.maybe_close():
                return False
            tab.shutdown()
            for other in self.document_tabs():
                if other is not tab:
                    other.document_closing(tab.document)
        self._tabs.pop(index)
        self.tab_bar.removeTab(index)
        self.stack.removeWidget(tab)
        tab.deleteLater()
        if isinstance(tab, DocumentTab):
            QTimer.singleShot(0, tab.document.doc.close)
        if self.tab_bar.count() == 1:
            self.tab_bar.setCurrentIndex(0)
        return True

    def close_current_tab(self) -> None:
        index = self.tab_bar.currentIndex()
        if index > 0:
            self.close_tab(index)

    def _tab_menu(self, pos) -> None:
        index = self.tab_bar.tabAt(pos)
        if index <= 0:
            return
        menu = QMenu(self)
        tab = self._tabs[index]
        menu.addAction(icons.icon("x"), self.tr("Cerrar"), lambda: self.close_tab(self._tabs.index(tab)))
        menu.addAction(self.tr("Cerrar las demás"), lambda: self._close_others(tab))
        if isinstance(tab, DocumentTab) and tab.document.path:
            menu.addSeparator()
            menu.addAction(icons.icon("folder-open"), self.tr("Mostrar en la carpeta"),
                           lambda: platform.reveal_in_folder(tab.document.path))
        menu.exec(self.tab_bar.mapToGlobal(pos))

    def _close_others(self, keep: QWidget) -> None:
        for widget in list(self._tabs):
            if widget is not keep and isinstance(widget, DocumentTab):
                if not self.close_tab(self._tabs.index(widget)):
                    return

    def next_tab(self, step: int = 1) -> None:
        count = self.tab_bar.count()
        if count > 1:
            self.tab_bar.setCurrentIndex((self.tab_bar.currentIndex() + step) % count)

    # ======================================================================
    # Abrir
    # ======================================================================
    def open_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(self, self.tr("Abrir"), settings().last_directory, open_any_filter())
        if paths:
            settings().last_directory = paths[0]
            self.open_files(paths)

    def open_files(self, paths: list[str]) -> None:
        last: DocumentTab | None = None
        for path in paths:
            tab = self._open_one(path)
            if tab is not None:
                last = tab
        if last is not None:
            self.tab_bar.setCurrentIndex(self._tabs.index(last))

    def open_and_raise(self, paths: list[str]) -> None:
        if self.isMinimized():
            self.showNormal()
        self.raise_()
        self.activateWindow()
        self.open_files(paths)

    def _find_open(self, path: str) -> DocumentTab | None:
        try:
            target = Path(path).resolve()
        except OSError:
            return None
        for tab in self.document_tabs():
            if tab.document.path:
                try:
                    if Path(tab.document.path).resolve() == target:
                        return tab
                except OSError:
                    continue
        return None

    def _open_one(self, path: str) -> DocumentTab | None:
        existing = self._find_open(path)
        if existing is not None:
            return existing
        if Path(path).suffix.lower() in MODEL_EXTENSIONS:
            self.open_model_file(path)
            return None
        if Path(path).suffix.lower() not in OPENABLE_EXTENSIONS:
            Toast.show_message(self, self.tr("«{0}» no es un formato compatible").format(Path(path).name), kind="warning")
            return None
        password = None
        retry = False
        while True:
            try:
                model = PdfDocument.open(path, password)
                break
            except PasswordRequiredError:
                pass
            except WrongPasswordError:
                retry = True
            except BlackPdfError as exc:
                show_error(self, exc)
                settings().remove_recent(path)
                return None
            except Exception as exc:
                show_error(self, exc, f"Abrir {path}")
                return None
            password = ask_password(self, path, retry)
            if password is None:
                return None
        tab = DocumentTab(model, self)
        tab.documents_provider = self._open_documents
        tab.set_tools_menu(tools.document_menu_entries())
        tab.titleChanged.connect(lambda t=tab: self._refresh_tab(t))
        tab.toolRequested.connect(lambda tool_id, preset, t=tab: self.launch_tool(tool_id, preset, tab=t))
        tab.openRequested.connect(self.open_files)
        tab.fullscreenRequested.connect(self.toggle_fullscreen)
        tab.document.historyChanged.connect(self._update_actions)
        index = self._add_page(tab, format_icon(model.format_suffix), self._tab_title(tab))
        self._refresh_tab(tab)
        self.tab_bar.setTabToolTip(index, model.path or model.display_name)
        if model.path:
            settings().add_recent(model.path)
        return tab

    def open_model_file(self, path: str) -> None:
        """Abre un archivo .u3d o .prc en el visor 3D."""
        from .model3d_window import Model3DWindow, show_window

        try:
            key = str(Path(path).resolve())
        except OSError:
            key = path
        window = self._model_windows.get(key)
        if window is not None:
            show_window(window)
            return
        window = Model3DWindow(Path(path).name, str(Path(path).parent), self)
        self._model_windows[key] = window
        window.closed.connect(lambda k=key: self._model_windows.pop(k, None))
        window.load("blackpdf.core.model3d:load_file", path)
        show_window(window)
        settings().add_recent(path)

    # ======================================================================
    # Herramientas
    # ======================================================================
    def _context_for(self, tab: DocumentTab | None) -> SourceContext | None:
        if tab is None:
            return None
        model = tab.document
        return SourceContext(
            name=model.display_name,
            path=model.path,
            page_count=model.page_count,
            modified=model.is_modified,
            encrypted=model.is_encrypted,
            snapshot=model.snapshot,
            document=model.doc,
        )

    def launch_tool(self, tool_id: str, preset: dict | None = None, tab: DocumentTab | None = None,
                    from_home: bool = False) -> None:
        item = tools.spec(tool_id)
        if item is None:
            return
        if tab is None and not from_home:
            tab = self.current_tab()
        if item.editor:
            self._launch_editor_tool(tool_id, tab)
            return
        dialog_class = tools.dialog_class(tool_id)
        if dialog_class is None:
            return
        dialog = dialog_class(self, self._context_for(tab), preset or {})
        dialog.openFiles.connect(self.open_files)
        dialog.exec()
        dialog.deleteLater()

    def _launch_editor_tool(self, tool_id: str, tab: DocumentTab | None) -> None:
        if tab is None:
            paths, _ = QFileDialog.getOpenFileNames(self, self.tr("Elegir documento"), settings().last_directory, open_filter())
            if not paths:
                return
            settings().last_directory = paths[0]
            self.open_files(paths[:1])
            tab = self.current_tab()
            if tab is None:
                return
        else:
            self.tab_bar.setCurrentIndex(self._tabs.index(tab))
        if tool_id == "forms":
            FormFillDialog(tab.document, self).exec()
        elif tool_id == "sign":
            tab.set_tool("signature")
        elif tool_id == "annotate":
            if not tab.annotation_bar.isVisible():
                tab.toggle_annotation_bar()
            tab.set_tool("pen")
        elif tool_id == "organize":
            tab.set_organize_mode(True)

    # ======================================================================
    # Acciones globales
    # ======================================================================
    def _action(self, name: str, shortcuts, slot, needs_document: bool = False) -> QAction:
        action = QAction(name, self)
        if isinstance(shortcuts, (list, tuple)):
            action.setShortcuts([QKeySequence(s) for s in shortcuts])
        elif shortcuts:
            action.setShortcuts(QKeySequence.keyBindings(shortcuts) if isinstance(shortcuts, QKeySequence.StandardKey) else [QKeySequence(shortcuts)])
        action.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        action.triggered.connect(slot)
        action.setProperty("needs_document", needs_document)
        self.addAction(action)
        return action

    def _on_tab(self, method: str):
        def call():
            tab = self.current_tab()
            if tab is not None:
                getattr(tab, method)()
        return call

    def _install_actions(self) -> None:
        self._actions = [
            self._action(self.tr("Abrir"), "Ctrl+O", self.open_dialog),
            self._action(self.tr("Cerrar pestaña"), ["Ctrl+W", "Ctrl+F4"], self.close_current_tab),
            self._action(self.tr("Pestaña siguiente"), ["Ctrl+Tab", "Ctrl+PgDown"], lambda: self.next_tab(1)),
            self._action(self.tr("Pestaña anterior"), ["Ctrl+Shift+Tab", "Ctrl+PgUp"], lambda: self.next_tab(-1)),
            self._action(self.tr("Paleta de comandos"), ["Ctrl+K", "Ctrl+Shift+P"], self.show_palette),
            self._action(self.tr("Guardar"), "Ctrl+S", self._on_tab("save"), True),
            self._action(self.tr("Guardar como"), "Ctrl+Shift+S", self._on_tab("save_as"), True),
            self._action(self.tr("Imprimir"), "Ctrl+P", self._on_tab("print_document"), True),
            self._action(self.tr("Deshacer"), "Ctrl+Z", self._on_tab("undo"), True),
            self._action(self.tr("Rehacer"), ["Ctrl+Y", "Ctrl+Shift+Z"], self._on_tab("redo"), True),
            self._action(self.tr("Buscar"), "Ctrl+F", self._on_tab("find"), True),
            self._action(self.tr("Acercar"), ["Ctrl++", "Ctrl+=", "+"], self._zoom_in, True),
            self._action(self.tr("Alejar"), ["Ctrl+-", "-"], self._zoom_out, True),
            self._action(self.tr("Pantalla completa"), "F11", self.toggle_fullscreen),
            self._action(self.tr("Salir"), "Ctrl+Q", self.close),
        ]

    def _zoom_in(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.viewer.zoom_in()

    def _zoom_out(self) -> None:
        tab = self.current_tab()
        if tab is not None:
            tab.viewer.zoom_out()

    def _update_actions(self) -> None:
        has_document = self.current_tab() is not None
        for action in self._actions:
            if action.property("needs_document"):
                action.setEnabled(has_document)

    def show_palette(self) -> None:
        self.palette_overlay.open(self._commands())

    def _commands(self) -> list[Command]:
        tr = self.tr
        commands = [
            Command(tr("Abrir archivo"), tr("Archivo"), "folder-open", self.open_dialog, shortcut_text("Ctrl+O"), "open"),
            Command(tr("Ir al inicio"), tr("Navegación"), "house", lambda: self.tab_bar.setCurrentIndex(0)),
            Command(tr("Cambiar tema claro u oscuro"), tr("Apariencia"), "sun", self.toggle_theme, keywords="tema modo"),
            Command(tr("Atajos de teclado"), tr("Ayuda"), "keyboard", lambda: ShortcutsDialog(self).exec()),
            Command(tr("Acerca de BlackPDF"), tr("Ayuda"), "info", lambda: AboutDialog(self).exec()),
        ]
        tab = self.current_tab()
        if tab is not None:
            doc = tr("Documento")
            commands += [
                Command(tr("Guardar"), doc, "save", tab.save, shortcut_text("Ctrl+S")),
                Command(tr("Guardar como..."), doc, "save", tab.save_as, shortcut_text("Ctrl+Shift+S")),
                Command(tr("Imprimir"), doc, "printer", tab.print_document, shortcut_text("Ctrl+P")),
                Command(tr("Deshacer"), doc, "undo-2", tab.undo, shortcut_text("Ctrl+Z")),
                Command(tr("Rehacer"), doc, "redo-2", tab.redo, shortcut_text("Ctrl+Y")),
                Command(tr("Buscar en el documento"), doc, "search", tab.find, shortcut_text("Ctrl+F")),
                Command(tr("Propiedades del documento"), doc, "info", tab.show_properties, shortcut_text("Ctrl+D")),
                Command(tr("Girar páginas a la derecha"), tr("Páginas"), "rotate-cw", lambda: tab.rotate_pages(90)),
                Command(tr("Girar páginas a la izquierda"), tr("Páginas"), "rotate-ccw", lambda: tab.rotate_pages(-90)),
                Command(tr("Eliminar páginas seleccionadas"), tr("Páginas"), "trash-2", tab.delete_pages, "Supr"),
                Command(tr("Duplicar páginas"), tr("Páginas"), "copy", tab.duplicate_pages),
                Command(tr("Insertar página en blanco"), tr("Páginas"), "file-plus-2", tab.insert_blank),
                Command(tr("Insertar páginas desde archivo"), tr("Páginas"), "file-input", tab.insert_from_file),
                Command(tr("Ajustar al ancho"), tr("Vista"), "move-horizontal", lambda: tab.viewer.set_fit_mode("width"), shortcut_text("Ctrl+1")),
                Command(tr("Página completa"), tr("Vista"), "scan", lambda: tab.viewer.set_fit_mode("page"), shortcut_text("Ctrl+2")),
                Command(tr("Tamaño real"), tr("Vista"), "zoom-in", lambda: tab.viewer.set_zoom(1.0), shortcut_text("Ctrl+0")),
                Command(tr("Modo noche"), tr("Vista"), "moon", lambda: tab.set_night_mode(not tab.viewer.night_mode), shortcut_text("Ctrl+M")),
                Command(tr("Panel lateral"), tr("Vista"), "panel-left", tab.toggle_sidebar, "F4"),
                Command(tr("Organizar páginas"), tr("Páginas"), "layout-grid", tab.toggle_organize, shortcut_text("Ctrl+G")),
                Command(tr("Vista dividida"), tr("Vista"), "columns-2", tab.toggle_split, shortcut_text("Ctrl+\\")),
                Command(tr("Comentarios"), tr("Comentarios"), "message-square", tab.show_comments),
                Command(tr("Comentar esta página"), tr("Comentarios"), "message-square-plus",
                        lambda: (tab.show_comments(), tab.comments.start_page_comment())),
                Command(tr("Añadir PDF al documento"), tr("Páginas"), "file-plus", tab.add_document_pages),
                Command(tr("Ver modelos 3D del documento"), tr("Vista"), "box", tab.show_models, keywords="3d u3d prc cad"),
                Command(tr("Pantalla completa"), tr("Vista"), "maximize", self.toggle_fullscreen, "F11"),
            ]
            for name, icon_name, title in (
                ("pen", "pencil", tr("Lápiz")), ("highlight", "highlighter", tr("Resaltar texto")),
                ("underline", "underline", tr("Subrayar texto")), ("strikeout", "strikethrough", tr("Tachar texto")),
                ("rect", "square", tr("Rectángulo")), ("ellipse", "circle", tr("Elipse")), ("arrow", "move-up-right", tr("Flecha")),
                ("line", "minus", tr("Línea")), ("text", "type", tr("Cuadro de texto")), ("note", "sticky-note", tr("Nota")),
                ("signature", "signature", tr("Firmar")), ("eraser", "eraser", tr("Goma de borrar anotaciones")),
            ):
                commands.append(Command(title, tr("Anotar"), icon_name, lambda n=name: tab.set_tool(n)))
        for item in tools.tool_specs():
            commands.append(Command(item.title, tr("Herramientas"), item.icon,
                                    lambda t=item.id: self.launch_tool(t), keywords=item.description))
        for path in settings().recent_files()[:8]:
            if Path(path).exists():
                commands.append(Command(Path(path).name, tr("Recientes"), "clock", lambda p=path: self.open_files([p]),
                                        keywords=path))
        return commands

    # ======================================================================
    # Tema y pantalla completa
    # ======================================================================
    def toggle_theme(self) -> None:
        self.set_theme("light" if theme.current().is_dark else "dark")

    def set_theme(self, mode: str) -> None:
        settings().theme = mode
        theme.manager().apply(mode)

    def _theme_changed(self) -> None:
        dark = theme.current().is_dark
        self.theme_button.set_icon_name("sun" if dark else "moon")
        self.theme_button.setToolTip(self.tr("Tema claro") if dark else self.tr("Tema oscuro"))
        platform.apply_title_bar_theme(self, dark)
        for widget in self.findChildren(QWidget):
            widget.update()

    def toggle_fullscreen(self) -> None:
        tab = self.current_tab()
        if self.isFullScreen():
            self.header.setVisible(True)
            for document_tab in self.document_tabs():
                document_tab.set_sidebar_visible(True)
            if self._fullscreen_restore:
                self.showMaximized()
            else:
                self.showNormal()
        else:
            self._fullscreen_restore = self.isMaximized()
            self.header.setVisible(False)
            if tab is not None:
                tab.set_sidebar_visible(False)
            self.showFullScreen()
            Toast.show_message(self, self.tr("Pulsa F11 para salir de pantalla completa"), duration=2500)

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape and self.isFullScreen():
            self.toggle_fullscreen()
            return
        super().keyPressEvent(event)

    # ======================================================================
    # Arrastrar y soltar, cierre
    # ======================================================================
    def dragEnterEvent(self, event) -> None:
        if dropped_paths(event, OPENABLE_EXTENSIONS + MODEL_EXTENSIONS):
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = dropped_paths(event, OPENABLE_EXTENSIONS + MODEL_EXTENSIONS)
        if paths:
            event.acceptProposedAction()
            self.open_files(paths)

    def closeEvent(self, event: QCloseEvent) -> None:
        for tab in list(self.document_tabs()):
            index = self._tabs.index(tab)
            self.tab_bar.setCurrentIndex(index)
            if not tab.maybe_close():
                event.ignore()
                return
        for tab in self.document_tabs():
            tab.shutdown()
        tasks.cancel_all()
        settings().set_window_geometry(self.saveGeometry())
        settings().sync()
        event.accept()
