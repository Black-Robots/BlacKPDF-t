"""Bienvenida la primera vez que se abre BlackPDF: idioma y tema."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QLabel, QLayout, QVBoxLayout, QWidget

from ... import APP_NAME, AUTHOR
from .. import i18n, icons, theme
from ..settings import settings
from ..widgets import Button, SectionLabel, SegmentedControl, label


class WelcomeDialog(QDialog):
    """Se elige el idioma y el tema; ambos se aplican al momento, sin reiniciar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(icons.app_icon())
        self._language = settings().language if settings().language in i18n.codes() else "auto"
        self._theme = theme.manager().mode
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 0, 0, 0)
        # La ventana toma siempre el tamaño del contenido (cambia al traducirse).
        self._root.setSizeConstraint(QLayout.SizeConstraint.SetFixedSize)
        self._content: QWidget | None = None
        self._build()

    def _build(self) -> None:
        """Crea el contenido con el idioma activo (se rehace al cambiar de idioma)."""
        if self._content is not None:
            self._root.removeWidget(self._content)
            self._content.hide()
            self._content.deleteLater()
        content = QWidget(self)
        content.setFixedWidth(480)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(36, 32, 36, 24)
        layout.setSpacing(12)
        logo = QLabel(content)
        logo.setPixmap(icons.app_logo(72, self.devicePixelRatioF()))
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(logo)
        name = label(APP_NAME, "hero")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(name)
        author = label(f"By {AUTHOR}", "muted")
        author.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(author)
        layout.addSpacing(4)
        self.greeting = label(self.tr("Te damos la bienvenida a BlackPDF"), "title")
        self.greeting.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.greeting)
        intro = label(self.tr(
            "Visor y editor de PDF libre y gratuito. Funciona sin conexión: tus documentos nunca salen de tu equipo."),
            "body", wrap=True)
        intro.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(intro)
        layout.addSpacing(10)

        layout.addWidget(SectionLabel(self.tr("Idioma")))
        self.language_box = QComboBox(content)
        system_name = i18n.language_name(i18n.system_language())
        self.language_box.addItem(self.tr("Según el sistema ({0})").format(system_name), "auto")
        for code, language_name in i18n.LANGUAGES:
            self.language_box.addItem(language_name, code)
        self.language_box.setCurrentIndex(max(0, self.language_box.findData(self._language)))
        self.language_box.currentIndexChanged.connect(self._language_changed)
        layout.addWidget(self.language_box)
        layout.addSpacing(4)

        layout.addWidget(SectionLabel(self.tr("Tema")))
        self.theme_switch = SegmentedControl(
            [("dark", self.tr("Oscuro")), ("light", self.tr("Claro")), ("system", self.tr("Según el sistema"))],
            content,
            icons_map={"dark": "moon", "light": "sun", "system": "settings-2"},
        )
        self.theme_switch.set_value(self._theme, emit=False)
        self.theme_switch.changed.connect(self._theme_changed)
        layout.addWidget(self.theme_switch)

        layout.addSpacing(4)
        hint = label(self.tr("Podrás cambiarlos cuando quieras desde el menú."), "caption", wrap=True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(hint)
        layout.addSpacing(8)
        row = QHBoxLayout()
        row.addStretch(1)
        start = Button(self.tr("Empezar"), "primary")
        start.setDefault(True)
        start.clicked.connect(self.accept)
        row.addWidget(start)
        layout.addLayout(row)

        self._root.addWidget(content)
        self._content = content
        start.setFocus()

    def _language_changed(self, index: int) -> None:
        self._language = self.language_box.itemData(index)
        i18n.install(self._language)
        # Se rehace después: el combo que emite la señal se destruye al reconstruir.
        QTimer.singleShot(0, self._build)

    def _theme_changed(self, mode: str) -> None:
        self._theme = mode
        theme.manager().apply(mode)

    def done(self, result: int) -> None:
        # Cerrar con la X también guarda lo elegido: la bienvenida no vuelve a salir.
        settings().language = self._language
        settings().theme = self._theme
        settings().welcomed = True
        settings().sync()
        super().done(result)
