"""Panel de comentarios: lista por página, edición, respuestas y navegación."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ..core import comments as core_comments
from ..core.errors import BlackPdfError
from . import icons, theme
from .document import PdfDocument
from .errors import show_error
from .settings import settings
from .widgets import Button, SectionLabel, label

KIND_ICONS = {
    "Text": "message-square",
    "FreeText": "type",
    "Highlight": "highlighter",
    "Underline": "underline",
    "StrikeOut": "strikethrough",
    "Squiggly": "underline",
    "Ink": "pencil",
    "Square": "square",
    "Circle": "circle",
    "Line": "move-up-right",
    "PolyLine": "pen-tool",
    "Polygon": "pen-tool",
    "Stamp": "stamp",
    "FileAttachment": "file-input",
}


def kind_label(kind: str, tr) -> str:
    return {
        "Text": tr("Comentario"),
        "FreeText": tr("Cuadro de texto"),
        "Highlight": tr("Resaltado"),
        "Underline": tr("Subrayado"),
        "StrikeOut": tr("Tachado"),
        "Squiggly": tr("Ondulado"),
        "Ink": tr("Dibujo"),
        "Square": tr("Rectángulo"),
        "Circle": tr("Elipse"),
        "Line": tr("Línea"),
        "Stamp": tr("Sello"),
    }.get(kind, kind)


class ColorDot(QLabel):
    def __init__(self, icon_name: str, color: QColor | None, parent=None):
        super().__init__(parent)
        self.icon_name = icon_name
        self.color = color
        self.setFixedSize(28, 28)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tint = QColor(self.color) if self.color is not None else theme.color("accent")
        fill = QColor(tint)
        fill.setAlphaF(0.18)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()), 8, 8)
        glyph = icons.pixmap(self.icon_name, 16, color=tint, dpr=self.devicePixelRatioF())
        painter.drawPixmap(6, 6, glyph)


class Composer(QWidget):
    """Campo para escribir un comentario o una respuesta."""

    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, placeholder: str, action: str, initial: str = "", parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(6)
        self.edit = QPlainTextEdit(initial)
        self.edit.setPlaceholderText(placeholder)
        self.edit.setFixedHeight(72)
        layout.addWidget(self.edit)
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = Button(self.tr("Cancelar"), "ghost")
        cancel.clicked.connect(self.cancelled)
        send = Button(action, "primary")
        send.clicked.connect(self._submit)
        row.addWidget(cancel)
        row.addWidget(send)
        layout.addLayout(row)

    def _submit(self) -> None:
        text = self.edit.toPlainText().strip()
        if text:
            self.submitted.emit(text)

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._submit()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        super().keyPressEvent(event)

    def focus(self) -> None:
        self.edit.setFocus()
        cursor = self.edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.edit.setTextCursor(cursor)


class CommentCard(QFrame):
    activated = Signal(object)
    editRequested = Signal(object, str)
    replyRequested = Signal(object, str)
    deleteRequested = Signal(object)

    def __init__(self, comment: core_comments.Comment, reply: bool = False, parent=None):
        super().__init__(parent)
        self.comment = comment
        self.setProperty("role", "panel" if not reply else "")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10 if not reply else 0, 10 if not reply else 6, 8, 10 if not reply else 2)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setSpacing(8)
        color = QColor.fromRgbF(*comment.color) if comment.color else None
        header.addWidget(ColorDot(KIND_ICONS.get(comment.kind, "sticky-note"), color))
        names = QVBoxLayout()
        names.setSpacing(0)
        author = label(comment.author or self.tr("Sin autor"), "")
        author.setStyleSheet("font-weight: 600;")
        names.addWidget(author)
        meta = kind_label(comment.kind, self.tr)
        if comment.modified:
            meta += "  ·  " + comment.modified
        names.addWidget(label(meta, "caption"))
        header.addLayout(names, 1)
        more = QToolButton(self)
        more.setIcon(icons.icon("ellipsis"))
        more.setIconSize(QSize(16, 16))
        more.setAutoRaise(True)
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction(icons.icon("pencil-line"), self.tr("Editar"), self._start_edit)
        menu.addAction(icons.icon("reply"), self.tr("Responder"), self._start_reply)
        menu.addSeparator()
        menu.addAction(icons.icon("trash-2", icons.ROLE_DANGER), self.tr("Eliminar"), lambda: self.deleteRequested.emit(comment))
        more.setMenu(menu)
        header.addWidget(more, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(header)

        if comment.quote:
            quote = label("«" + comment.quote[:240] + ("..." if len(comment.quote) > 240 else "") + "»", "muted", wrap=True)
            quote.setStyleSheet(
                f"font-style: italic; border-left: 2px solid {theme.current().border_strong}; padding-left: 8px;"
            )
            layout.addWidget(quote)
        if comment.content:
            body = label(comment.content, "body", wrap=True)
            body.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            body.setCursor(Qt.CursorShape.IBeamCursor)
            layout.addWidget(body)
        elif comment.kind != "Text":
            add = Button(self.tr("Añadir comentario"), "link")
            add.clicked.connect(self._start_edit)
            layout.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)

        self.replies_box = QVBoxLayout()
        self.replies_box.setContentsMargins(18, 2, 0, 0)
        self.replies_box.setSpacing(6)
        for child in comment.replies:
            card = CommentCard(child, reply=True, parent=self)
            card.activated.connect(self.activated)
            card.editRequested.connect(self.editRequested)
            card.replyRequested.connect(self.replyRequested)
            card.deleteRequested.connect(self.deleteRequested)
            self.replies_box.addWidget(card)
        layout.addLayout(self.replies_box)
        self.composer_slot = QVBoxLayout()
        layout.addLayout(self.composer_slot)
        self._composer: Composer | None = None

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.activated.emit(self.comment)
        super().mouseReleaseEvent(event)

    def _open_composer(self, placeholder: str, action: str, initial: str, handler) -> None:
        self._close_composer()
        composer = Composer(placeholder, action, initial, self)
        composer.submitted.connect(handler)
        composer.cancelled.connect(self._close_composer)
        self.composer_slot.addWidget(composer)
        self._composer = composer
        QTimer.singleShot(0, composer.focus)

    def _close_composer(self) -> None:
        if self._composer is not None:
            self._composer.hide()
            self._composer.deleteLater()
            self._composer = None

    def _start_edit(self) -> None:
        self._open_composer(self.tr("Escribe el comentario"), self.tr("Guardar"), self.comment.content,
                            lambda text: self.editRequested.emit(self.comment, text))

    def _start_reply(self) -> None:
        self._open_composer(self.tr("Escribe una respuesta"), self.tr("Responder"), "",
                            lambda text: self.replyRequested.emit(self.comment, text))

    def start_edit(self) -> None:
        self._start_edit()


class CommentsPanel(QWidget):
    """Lista de comentarios del documento agrupados por página."""

    commentActivated = Signal(int, QRectF)
    countChanged = Signal(int)

    def __init__(self, document: PdfDocument, current_page, parent=None):
        super().__init__(parent)
        self.document = document
        self.current_page = current_page
        self._dirty = True
        self._pending_edit: int | None = None
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.add_button = Button(self.tr("Comentar esta página"), "", "message-square-plus")
        self.add_button.clicked.connect(self.start_page_comment)
        layout.addWidget(self.add_button)
        self.composer_slot = QVBoxLayout()
        layout.addLayout(self.composer_slot)
        self._composer: Composer | None = None

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.body = QWidget()
        self.list_layout = QVBoxLayout(self.body)
        self.list_layout.setContentsMargins(0, 0, 4, 0)
        self.list_layout.setSpacing(8)
        self.scroll.setWidget(self.body)
        layout.addWidget(self.scroll, 1)

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self.refresh)
        document.changed.connect(self._on_changed)

    # ---------- actualización ----------
    def _on_changed(self, pages) -> None:
        self._dirty = True
        if self.isVisible():
            self._refresh_timer.start()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._dirty:
            self.refresh()

    def refresh(self) -> None:
        self._dirty = False
        while self.list_layout.count():
            item = self.list_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.setParent(None)
                widget.deleteLater()
        try:
            comments = core_comments.list_comments(self.document.doc)
        except Exception:
            comments = []
        total = sum(1 + self._count_replies(c) for c in comments)
        self.countChanged.emit(total)
        if not comments:
            empty = QWidget()
            empty_layout = QVBoxLayout(empty)
            empty_layout.setContentsMargins(8, 32, 8, 8)
            glyph = QLabel()
            glyph.setPixmap(icons.pixmap("messages-square", 32, icons.ROLE_MUTED, self.devicePixelRatioF()))
            glyph.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(glyph)
            text = label(self.tr("Aún no hay comentarios. Resalta texto, añade notas o comenta una página."),
                         "muted", wrap=True)
            text.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty_layout.addWidget(text)
            self.list_layout.addWidget(empty)
            self.list_layout.addStretch(1)
            return
        current_page = -1
        target_card: CommentCard | None = None
        for comment in comments:
            if comment.page != current_page:
                current_page = comment.page
                self.list_layout.addWidget(SectionLabel(self.tr("Página {0}").format(current_page + 1)))
            card = CommentCard(comment)
            card.activated.connect(lambda c: self.commentActivated.emit(c.page, QRectF(c.rect.x0, c.rect.y0, c.rect.width, c.rect.height)))
            card.editRequested.connect(self._edit)
            card.replyRequested.connect(self._reply)
            card.deleteRequested.connect(self._delete)
            self.list_layout.addWidget(card)
            if self._pending_edit is not None and comment.xref == self._pending_edit:
                target_card = card
        self.list_layout.addStretch(1)
        if target_card is not None:
            self._pending_edit = None
            QTimer.singleShot(0, lambda: (self.scroll.ensureWidgetVisible(target_card), target_card.start_edit()))

    @staticmethod
    def _count_replies(comment) -> int:
        return sum(1 + CommentsPanel._count_replies(r) for r in comment.replies)

    def edit_comment_later(self, xref: int) -> None:
        """Abre el editor del comentario `xref` en cuanto aparezca en la lista."""
        self._pending_edit = xref
        self._dirty = True
        if self.isVisible():
            self.refresh()

    # ---------- acciones ----------
    def _apply(self, label_text: str, page: int, operation) -> object:
        try:
            return self.document.edit(label_text, lambda doc: operation(doc[page]), pages=[page])
        except BlackPdfError as exc:
            show_error(self, exc)
        return None

    def _edit(self, comment, text: str) -> None:
        self._apply(self.tr("Editar comentario"), comment.page,
                    lambda page: core_comments.set_comment(page, comment.xref, text, settings().author_name))

    def _reply(self, comment, text: str) -> None:
        self._apply(self.tr("Responder comentario"), comment.page,
                    lambda page: core_comments.add_reply(page, comment.xref, text, settings().author_name))

    def _delete(self, comment) -> None:
        self._apply(self.tr("Eliminar comentario"), comment.page,
                    lambda page: core_comments.delete_comment(page, comment.xref))

    def start_page_comment(self) -> None:
        page = self.current_page()
        if self._composer is not None:
            self._composer.focus()
            return
        composer = Composer(self.tr("Comentario para la página {0}").format(page + 1), self.tr("Comentar"), "", self)
        composer.submitted.connect(lambda text: self._add_page_comment(page, text))
        composer.cancelled.connect(self._close_composer)
        self.composer_slot.addWidget(composer)
        self._composer = composer
        QTimer.singleShot(0, composer.focus)

    def _close_composer(self) -> None:
        if self._composer is not None:
            self._composer.hide()
            self._composer.deleteLater()
            self._composer = None

    def _add_page_comment(self, page: int, text: str) -> None:
        self._close_composer()
        self._apply(self.tr("Comentar página"), page,
                    lambda pg: core_comments.add_page_comment(pg, text, settings().author_name))
