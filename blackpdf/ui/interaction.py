"""Herramientas de interacción del visor: seleccionar, mano y anotar.

Cada herramienta recibe los eventos de ratón del visor, dibuja su vista
previa en coordenadas de página (puntos) y, al terminar, aplica la
anotación al documento mediante `PdfDocument.edit` (con deshacer).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pymupdf
from PySide6.QtCore import QEvent, QObject, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import QInputDialog, QPlainTextEdit

from ..core import annotations as core_annots
from ..core import measure as core_measure
from ..core import text as core_text
from ..core.errors import BlackPdfError
from . import icons, theme
from .settings import settings
from .measure import format_measure
from .stamps import StampChoice, stamp_image
from .viewer import PageHit, PdfViewer, to_rect

DRAG_THRESHOLD = 3


# ======================================================================
# Estilos
# ======================================================================
PRESET_COLORS = (
    "#111114", "#6B6B76", "#FFFFFF", "#E5484D", "#F76B15", "#FFC53D",
    "#30A46C", "#12A594", "#0090FF", "#3E63DD", "#8E4EC6", "#D6409F",
)


@dataclass
class ToolStyle:
    color: str
    width: float = 2.0
    opacity: float = 1.0
    font_size: float = 14.0

    def qcolor(self) -> QColor:
        return QColor(self.color)

    def rgb(self) -> tuple[float, float, float]:
        color = QColor(self.color)
        return color.redF(), color.greenF(), color.blueF()


DEFAULT_STYLES = {
    "pen": ToolStyle("#E5484D", 2.0),
    "highlight": ToolStyle("#FFC53D", 0, 1.0),
    "underline": ToolStyle("#0090FF", 0),
    "strikeout": ToolStyle("#E5484D", 0),
    "rect": ToolStyle("#E5484D", 2.0),
    "ellipse": ToolStyle("#E5484D", 2.0),
    "line": ToolStyle("#E5484D", 2.0),
    "arrow": ToolStyle("#E5484D", 2.0),
    "polygon": ToolStyle("#E5484D", 2.0),
    "measure": ToolStyle("#0D74CE", 1.2),
    "text": ToolStyle("#111114", 0, 1.0, 14.0),
    "note": ToolStyle("#FFC53D", 0),
    "signature": ToolStyle("#1B2A6B", 1.6),
}


def load_style(tool: str) -> ToolStyle:
    base = DEFAULT_STYLES.get(tool, ToolStyle("#E5484D"))
    stored = settings().tool_style(tool)
    try:
        return ToolStyle(
            color=str(stored.get("color", base.color)),
            width=float(stored.get("width", base.width)),
            opacity=float(stored.get("opacity", base.opacity)),
            font_size=float(stored.get("font_size", base.font_size)),
        )
    except (TypeError, ValueError, AttributeError):
        return ToolStyle(base.color, base.width, base.opacity, base.font_size)


def save_style(tool: str, style: ToolStyle) -> None:
    settings().set_tool_style(
        tool, {"color": style.color, "width": style.width, "opacity": style.opacity, "font_size": style.font_size}
    )


def make_cursor(icon_name: str, hot_x: int, hot_y: int, size: int = 22) -> QCursor:
    """Cursor a partir de un icono, con halo claro para verse sobre cualquier fondo."""
    dpr = 2.0
    side = int(size * dpr)
    canvas = QPixmap(side + 4, side + 4)
    canvas.fill(Qt.GlobalColor.transparent)
    painter = QPainter(canvas)
    halo = icons.pixmap(icon_name, size, dpr=dpr, color=QColor("#FFFFFF"))
    for dx, dy in ((-2, 0), (2, 0), (0, -2), (0, 2), (-1, -1), (1, 1), (-1, 1), (1, -1)):
        painter.drawPixmap(2 + dx, 2 + dy, halo)
    painter.drawPixmap(2, 2, icons.pixmap(icon_name, size, dpr=dpr, color=QColor("#17171C")))
    painter.end()
    canvas.setDevicePixelRatio(dpr)
    return QCursor(canvas, hot_x, hot_y)


# ======================================================================
# Base
# ======================================================================
class ViewerTool(QObject):
    name = "base"
    finished = Signal()

    def __init__(self, viewer: PdfViewer):
        super().__init__(viewer)
        self.viewer = viewer

    @property
    def model(self):
        return self.viewer.model

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.ArrowCursor)

    def activate(self) -> None:
        pass

    def deactivate(self) -> None:
        self.cancel()

    def press(self, event: QMouseEvent) -> None:
        pass

    def move(self, event: QMouseEvent) -> None:
        pass

    def release(self, event: QMouseEvent) -> None:
        pass

    def hover(self, event: QMouseEvent) -> None:
        self._hover_links(event)

    def double_click(self, event: QMouseEvent) -> None:
        pass

    def key_press(self, event: QKeyEvent) -> bool:
        return False

    def cancel(self) -> None:
        pass

    def viewport_changed(self) -> None:
        pass

    def paint_page(self, painter: QPainter, page: int) -> None:
        pass

    def update(self) -> None:
        self.viewer.viewport().update()

    def _hover_links(self, event: QMouseEvent) -> bool:
        hit = self.viewer.hit_test(event.position())
        link = self.viewer.link_at(hit)
        model = self.viewer.model_at(hit) if link is None else None
        self.viewer.set_hover_model(model)
        if link is not None:
            self.viewer.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            self.viewer.viewport().setToolTip(link.uri or self.tr("Ir a la página {0}").format(link.page + 1))
            return True
        if model is not None:
            self.viewer.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            self.viewer.viewport().setToolTip(self.tr("Abrir el modelo 3D"))
            return True
        self.viewer.viewport().setCursor(self.cursor())
        self.viewer.viewport().setToolTip("")
        return False

    def apply(self, label: str, page: int, operation, rewrite: bool = False) -> bool:
        """Aplica una anotación con deshacer; los errores previstos se ignoran."""
        try:
            self.model.edit(label, lambda doc: operation(doc[page]), pages=[page], rewrite=rewrite)
        except BlackPdfError:
            return False
        return True


# ======================================================================
# Navegación
# ======================================================================
class HandTool(ViewerTool):
    name = "hand"

    def __init__(self, viewer):
        super().__init__(viewer)
        self._press: QPointF | None = None
        self._moved = False

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.OpenHandCursor)

    def press(self, event):
        self._press = event.position()
        self._moved = False
        self.viewer.start_pan(event.position())

    def move(self, event):
        if self._press is not None:
            if (event.position() - self._press).manhattanLength() > DRAG_THRESHOLD:
                self._moved = True
            self.viewer.pan_to(event.position())

    def release(self, event):
        self.viewer.end_pan()
        if not self._moved:
            self.viewer.activate_at(event.position())
        self._press = None
        self.viewer.viewport().setCursor(self.cursor())


class SelectTool(ViewerTool):
    """Selección de texto en orden de lectura; clic en enlaces los sigue."""

    name = "select"

    def __init__(self, viewer):
        super().__init__(viewer)
        self._anchor: PageHit | None = None
        self._dragging = False

    def cursor(self) -> QCursor:
        return QCursor(Qt.CursorShape.IBeamCursor)

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        self._anchor = hit
        self._dragging = False
        if hit is None:
            self.viewer.clear_selection()

    def move(self, event):
        if self._anchor is None:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is None or hit.page != self._anchor.page:
            return
        if not self._dragging and (hit.point - self._anchor.point).manhattanLength() * self.viewer.scale < DRAG_THRESHOLD:
            return
        self._dragging = True
        words = self.viewer.words(hit.page)
        start = pymupdf.Point(self._anchor.point.x(), self._anchor.point.y())
        stop = pymupdf.Point(hit.point.x(), hit.point.y())
        self.viewer.set_selection(hit.page, core_text.select_words(words, start, stop))

    def release(self, event):
        if not self._dragging:
            if not self.viewer.activate_at(event.position()):
                self.viewer.clear_selection()
        self._anchor = None
        self._dragging = False

    def double_click(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        point = pymupdf.Point(hit.point.x(), hit.point.y())
        for word in self.viewer.words(hit.page):
            if point in word.rect:
                self.viewer.set_selection(hit.page, [word])
                return


# ======================================================================
# Anotación
# ======================================================================
class AnnotationTool(ViewerTool):
    """Base de las herramientas que crean anotaciones."""

    def __init__(self, viewer, name: str | None = None):
        super().__init__(viewer)
        if name:
            self.name = name
        self.style = load_style(self.name)

    def set_style(self, style: ToolStyle) -> None:
        self.style = style
        save_style(self.name, style)
        self.update()

    def hover(self, event):
        self.viewer.viewport().setCursor(self.cursor())


def _simplify(points: list[QPointF], tolerance: float) -> list[QPointF]:
    """Douglas-Peucker: reduce puntos manteniendo la forma del trazo."""
    if len(points) < 3:
        return points
    start, end = points[0], points[-1]
    dx, dy = end.x() - start.x(), end.y() - start.y()
    length = math.hypot(dx, dy) or 1e-9
    index, distance = 0, 0.0
    for i in range(1, len(points) - 1):
        p = points[i]
        d = abs(dy * p.x() - dx * p.y() + end.x() * start.y() - end.y() * start.x()) / length
        if d > distance:
            index, distance = i, d
    if distance > tolerance:
        left = _simplify(points[: index + 1], tolerance)
        right = _simplify(points[index:], tolerance)
        return left[:-1] + right
    return [start, end]


def _smooth(points: list[QPointF]) -> list[QPointF]:
    if len(points) < 4:
        return points
    result = [points[0]]
    for i in range(1, len(points) - 1):
        a, b, c = points[i - 1], points[i], points[i + 1]
        result.append(QPointF((a.x() + 2 * b.x() + c.x()) / 4, (a.y() + 2 * b.y() + c.y()) / 4))
    result.append(points[-1])
    return result


class PenTool(AnnotationTool):
    name = "pen"

    def __init__(self, viewer):
        super().__init__(viewer)
        self._page = -1
        self._points: list[QPointF] = []

    def cursor(self):
        return make_cursor("pencil", 2, 20)

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        self._page = hit.page
        self._points = [hit.point]

    def move(self, event):
        if self._page < 0:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is None or hit.page != self._page:
            return
        last = self._points[-1]
        if (hit.point - last).manhattanLength() * self.viewer.scale >= 1.5:
            self._points.append(hit.point)
            self.update()

    def release(self, event):
        if self._page < 0:
            return
        page, points = self._page, _smooth(self._points)
        points = _simplify(points, 0.25)
        self.cancel()
        style = self.style
        stroke = [(p.x(), p.y()) for p in points]
        self.apply(
            self.tr("Dibujo a mano alzada"),
            page,
            lambda pg: core_annots.add_ink(pg, [stroke], style.rgb(), style.width, style.opacity),
        )

    def cancel(self):
        self._page = -1
        self._points = []
        self.update()

    def paint_page(self, painter, page):
        if page != self._page or not self._points:
            return
        pen = QPen(self.style.qcolor(), max(self.style.width, 0.5))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(self._points[0])
        for point in self._points[1:]:
            path.lineTo(point)
        if len(self._points) == 1:
            path.lineTo(self._points[0] + QPointF(0.01, 0.01))
        painter.drawPath(path)


class MarkupTool(AnnotationTool):
    """Resaltar, subrayar o tachar texto. En páginas sin texto, por zona."""

    def __init__(self, viewer, kind: str):
        super().__init__(viewer, kind)
        self.kind = kind
        self._page = -1
        self._start = QPointF()
        self._end = QPointF()
        self._rects: list[QRectF] = []
        self._area_mode = False

    def cursor(self):
        return QCursor(Qt.CursorShape.IBeamCursor) if not self._area_mode else QCursor(Qt.CursorShape.CrossCursor)

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        self._page = hit.page
        self._start = self._end = hit.point
        self._area_mode = not self.viewer.words(hit.page)
        self._rects = []

    def move(self, event):
        if self._page < 0:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is None or hit.page != self._page:
            return
        self._end = hit.point
        if self._area_mode:
            self._rects = [QRectF(self._start, self._end).normalized()]
        else:
            words = core_text.select_words(
                self.viewer.words(self._page),
                pymupdf.Point(self._start.x(), self._start.y()),
                pymupdf.Point(self._end.x(), self._end.y()),
            )
            self._rects = [to_rect(r) for r in core_text.line_rects(words)]
        self.update()

    def release(self, event):
        if self._page < 0:
            return
        page, rects = self._page, list(self._rects)
        moved = (self._end - self._start).manhattanLength() * self.viewer.scale >= DRAG_THRESHOLD
        if not moved and not self._area_mode:
            point = pymupdf.Point(self._start.x(), self._start.y())
            rects = [to_rect(w.rect) for w in self.viewer.words(page) if point in w.rect]
        self.cancel()
        rects = [r for r in rects if r.width() > 1 and r.height() > 1]
        if not rects:
            return
        style, kind = self.style, self.kind
        labels = {
            "highlight": self.tr("Resaltar"),
            "underline": self.tr("Subrayar"),
            "strikeout": self.tr("Tachar"),
        }
        pdf_rects = [pymupdf.Rect(r.left(), r.top(), r.right(), r.bottom()) for r in rects]
        self.apply(
            labels.get(kind, kind),
            page,
            lambda pg: core_annots.add_text_markup(pg, kind, pdf_rects, style.rgb(), style.opacity),
        )

    def cancel(self):
        self._page = -1
        self._rects = []
        self.update()

    def paint_page(self, painter, page):
        if page != self._page or not self._rects:
            return
        color = self.style.qcolor()
        if self.kind == "highlight":
            color.setAlphaF(0.4)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            for rect in self._rects:
                painter.drawRect(rect)
            return
        pen = QPen(color, max(1.0, min(r.height() for r in self._rects) * 0.08))
        painter.setPen(pen)
        for rect in self._rects:
            y = rect.bottom() - rect.height() * 0.08 if self.kind == "underline" else rect.center().y()
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))


class ShapeTool(AnnotationTool):
    def __init__(self, viewer, kind: str):
        super().__init__(viewer, kind)
        self.kind = kind
        self._page = -1
        self._start = QPointF()
        self._end = QPointF()

    def cursor(self):
        return QCursor(Qt.CursorShape.CrossCursor)

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        self._page = hit.page
        self._start = self._end = hit.point

    def move(self, event):
        if self._page < 0:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is None or hit.page != self._page:
            return
        end = hit.point
        if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            end = self._constrain(end)
        self._end = end
        self.update()

    def _constrain(self, end: QPointF) -> QPointF:
        dx, dy = end.x() - self._start.x(), end.y() - self._start.y()
        if self.kind in ("rect", "ellipse"):
            side = max(abs(dx), abs(dy))
            return QPointF(self._start.x() + math.copysign(side, dx or 1), self._start.y() + math.copysign(side, dy or 1))
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        length = math.hypot(dx, dy)
        return QPointF(self._start.x() + length * math.cos(angle), self._start.y() + length * math.sin(angle))

    def release(self, event):
        if self._page < 0:
            return
        page, start, end = self._page, self._start, self._end
        self.cancel()
        if (end - start).manhattanLength() * self.viewer.scale < DRAG_THRESHOLD * 2:
            return
        style, kind = self.style, self.kind
        labels = {
            "rect": self.tr("Rectángulo"),
            "ellipse": self.tr("Elipse"),
            "line": self.tr("Línea"),
            "arrow": self.tr("Flecha"),
        }
        self.apply(
            labels.get(kind, kind),
            page,
            lambda pg: core_annots.add_shape(
                pg, kind, (start.x(), start.y()), (end.x(), end.y()), style.rgb(), style.width, None, style.opacity
            ),
        )

    def cancel(self):
        self._page = -1
        self.update()

    def paint_page(self, painter, page):
        if page != self._page:
            return
        color = self.style.qcolor()
        pen = QPen(color, max(self.style.width, 0.5))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        rect = QRectF(self._start, self._end).normalized()
        if self.kind == "rect":
            painter.drawRect(rect)
        elif self.kind == "ellipse":
            painter.drawEllipse(rect)
        else:
            painter.drawLine(self._start, self._end)
            if self.kind == "arrow":
                self._paint_arrow_head(painter, color)

    def _paint_arrow_head(self, painter: QPainter, color: QColor) -> None:
        dx, dy = self._end.x() - self._start.x(), self._end.y() - self._start.y()
        length = math.hypot(dx, dy)
        if length < 1:
            return
        ux, uy = dx / length, dy / length
        size = max(6.0, self.style.width * 4.5)
        base = QPointF(self._end.x() - ux * size, self._end.y() - uy * size)
        left = QPointF(base.x() - uy * size * 0.5, base.y() + ux * size * 0.5)
        right = QPointF(base.x() + uy * size * 0.5, base.y() - ux * size * 0.5)
        path = QPainterPath(self._end)
        path.lineTo(left)
        path.lineTo(right)
        path.closeSubpath()
        painter.setBrush(color)
        painter.drawPath(path)


class PolygonTool(AnnotationTool):
    """Polígono vértice a vértice: clic en cada uno; doble clic, Intro o clic en el primero para cerrar."""

    name = "polygon"
    CLOSE_DISTANCE = 8.0
    """Píxeles alrededor del primer vértice en los que un clic cierra la figura."""

    def __init__(self, viewer):
        super().__init__(viewer)
        self._page = -1
        self._points: list[QPointF] = []
        self._cursor: QPointF | None = None

    @property
    def busy(self) -> bool:
        return bool(self._points)

    def cursor(self):
        return QCursor(Qt.CursorShape.CrossCursor)

    def _near_first(self, point: QPointF) -> bool:
        if len(self._points) < 3:
            return False
        distance = math.hypot(point.x() - self._points[0].x(), point.y() - self._points[0].y())
        return distance * self.viewer.scale <= self.CLOSE_DISTANCE

    def press(self, event):
        hit = self.viewer.hit_test(event.position(), clamp=bool(self._points))
        if hit is None or (self._points and hit.page != self._page):
            return
        if self._near_first(hit.point):
            self.finish()
            return
        self._page = hit.page
        point = hit.point
        if self._points and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            point = self._constrain(point)
        self._points.append(point)
        self.update()

    def _constrain(self, point: QPointF) -> QPointF:
        last = self._points[-1]
        dx, dy = point.x() - last.x(), point.y() - last.y()
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        length = math.hypot(dx, dy)
        return QPointF(last.x() + length * math.cos(angle), last.y() + length * math.sin(angle))

    def hover(self, event):
        super().hover(event)
        if not self._points:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is not None and hit.page == self._page:
            point = hit.point
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                point = self._constrain(point)
            self._cursor = point
            self.update()

    def move(self, event):
        self.hover(event)

    def double_click(self, event):
        self.finish()

    def key_press(self, event) -> bool:
        if not self._points:
            return False
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish()
            return True
        if event.key() == Qt.Key.Key_Backspace:
            self._points.pop()
            self.update()
            return True
        return False

    def finish(self) -> None:
        page, points = self._page, [(p.x(), p.y()) for p in self._points]
        self.cancel()
        if len(points) < 3:
            return
        style = self.style
        self.apply(self.tr("Polígono"), page, lambda pg: core_annots.add_polygon(
            pg, points, style.rgb(), style.width, None, style.opacity))

    def cancel(self):
        self._page = -1
        self._points = []
        self._cursor = None
        self.update()

    def paint_page(self, painter, page):
        if page != self._page or not self._points:
            return
        color = self.style.qcolor()
        pen = QPen(color, max(self.style.width, 0.5))
        pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(self._points[0])
        for point in self._points[1:]:
            path.lineTo(point)
        if self._cursor is not None:
            path.lineTo(self._cursor)
        painter.drawPath(path)
        if len(self._points) >= 2 and self._cursor is not None:
            guide = QPen(color, 1.0 / max(self.viewer.scale, 0.1), Qt.PenStyle.DashLine)
            painter.setPen(guide)
            painter.drawLine(self._cursor, self._points[0])
        radius = 3.5 / max(self.viewer.scale, 0.1)
        painter.setPen(QPen(theme.color("accent"), 1.2 / max(self.viewer.scale, 0.1)))
        for index, point in enumerate(self._points):
            closing = index == 0 and self._cursor is not None and self._near_first(self._cursor)
            painter.setBrush(theme.color("accent") if closing else QColor("#FFFFFF"))
            painter.drawEllipse(point, radius * (1.6 if closing else 1.0), radius * (1.6 if closing else 1.0))


class TextTool(AnnotationTool):
    """Cuadro de texto editable sobre la página; se confirma al salir del campo."""

    name = "text"

    def __init__(self, viewer):
        super().__init__(viewer)
        self._editor: QPlainTextEdit | None = None
        self._page = -1
        self._origin = QPointF()

    def cursor(self):
        return QCursor(Qt.CursorShape.IBeamCursor)

    def press(self, event):
        if self._editor is not None:
            self.commit()
            return
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        self._page = hit.page
        self._origin = hit.point
        self._open_editor()

    def _open_editor(self) -> None:
        editor = _InlineTextEdit(self.viewer.viewport())
        editor.committed.connect(self.commit)
        editor.cancelled.connect(self.cancel)
        self._editor = editor
        self._style_editor()
        self.viewport_changed()
        editor.show()
        editor.setFocus(Qt.FocusReason.MouseFocusReason)

    def _style_editor(self) -> None:
        if self._editor is None:
            return
        font = QFont("Helvetica")
        font.setStyleHint(QFont.StyleHint.SansSerif)
        font.setPixelSize(max(6, round(self.style.font_size * self.viewer.scale)))
        self._editor.setFont(font)
        p = theme.current()
        self._editor.setStyleSheet(
            "QPlainTextEdit { background: rgba(255,255,255,0.92); color: %s; border: 1px dashed %s;"
            " border-radius: 2px; padding: 0px; }" % (self.style.color, p.accent)
        )

    def viewport_changed(self) -> None:
        if self._editor is None or self._page < 0 or self._page >= self.model.page_count:
            return
        self._style_editor()
        pos = self.viewer.page_to_viewport(self._page, self._origin)
        metrics = QFontMetricsF(self._editor.font())
        lines = self._editor.toPlainText().splitlines() or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines) + metrics.averageCharWidth() * 3
        height = metrics.lineSpacing() * (len(lines) + 0.6) + 8
        self._editor.setGeometry(int(pos.x()), int(pos.y()), int(max(width, 120)), int(height))

    def commit(self) -> None:
        if self._editor is None:
            return
        text = self._editor.toPlainText().rstrip()
        page, origin = self._page, self._origin
        self._close_editor()
        if not text.strip() or page < 0:
            return
        style = self.style
        width, height = core_annots.text_box_size(text, style.font_size)
        size = self.model.page_size(page)
        x0 = min(origin.x(), max(0.0, size.width() - width))
        y0 = min(origin.y(), max(0.0, size.height() - height))
        rect = pymupdf.Rect(x0, y0, x0 + width, y0 + height)
        self.apply(
            self.tr("Cuadro de texto"),
            page,
            lambda pg: core_annots.add_text_box(pg, rect, text, style.font_size, style.rgb()),
        )

    def _close_editor(self) -> None:
        editor, self._editor = self._editor, None
        if editor is not None:
            editor.blockSignals(True)
            editor.hide()
            editor.deleteLater()
        self._page = -1
        self.viewer.setFocus()

    def cancel(self) -> None:
        self._close_editor()

    def deactivate(self) -> None:
        self.commit()


class _InlineTextEdit(QPlainTextEdit):
    committed = Signal()
    cancelled = Signal()

    def __init__(self, parent):
        super().__init__(parent)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.setPlaceholderText(self.tr("Escribe aquí"))
        self.document().setDocumentMargin(4)
        self.textChanged.connect(self._grow)

    def _grow(self) -> None:
        tool_parent = self.parent()
        metrics = QFontMetricsF(self.font())
        lines = self.toPlainText().splitlines() or [""]
        width = max(metrics.horizontalAdvance(line) for line in lines) + metrics.averageCharWidth() * 3
        height = metrics.lineSpacing() * (len(lines) + 0.6) + 8
        self.resize(int(max(width, 120)), int(height))
        if tool_parent is not None:
            self.raise_()

    def event(self, event) -> bool:
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.committed.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.committed.emit()


class NoteTool(AnnotationTool):
    name = "note"

    def cursor(self):
        return make_cursor("sticky-note", 4, 4)

    def release(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        text, ok = QInputDialog.getMultiLineText(self.viewer, self.tr("Nueva nota"), self.tr("Texto de la nota:"))
        if not ok or not text.strip():
            return
        style, point = self.style, hit.point
        self.apply(
            self.tr("Nota"),
            hit.page,
            lambda pg: core_annots.add_note(pg, (point.x(), point.y()), text.strip(), style.rgb()),
        )


class SignatureTool(AnnotationTool):
    """Estampa la firma guardada: clic para colocarla o arrastrar para dar tamaño."""

    name = "signature"
    DEFAULT_WIDTH = 150.0

    def __init__(self, viewer):
        super().__init__(viewer)
        self.strokes: list = []
        self._hover: PageHit | None = None
        self._drag_page = -1
        self._start = QPointF()
        self._end = QPointF()

    def set_strokes(self, strokes) -> None:
        self.strokes = strokes
        self.update()

    def cursor(self):
        return QCursor(Qt.CursorShape.CrossCursor)

    def _aspect(self) -> float:
        return core_annots.strokes_aspect(self.strokes) if self.strokes else 3.0

    def _ghost_rect(self, center: QPointF) -> QRectF:
        width = self.DEFAULT_WIDTH
        height = width / self._aspect()
        return QRectF(center.x() - width / 2, center.y() - height / 2, width, height)

    def hover(self, event):
        super().hover(event)
        self._hover = self.viewer.hit_test(event.position())
        self.update()

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None or not self.strokes:
            return
        self._drag_page = hit.page
        self._start = self._end = hit.point

    def move(self, event):
        if self._drag_page < 0:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is not None and hit.page == self._drag_page:
            self._end = hit.point
            self.update()

    def release(self, event):
        if self._drag_page < 0:
            return
        page = self._drag_page
        drag = QRectF(self._start, self._end).normalized()
        if drag.width() * self.viewer.scale < 8 or drag.height() * self.viewer.scale < 8:
            drag = self._ghost_rect(self._start)
        self._drag_page = -1
        rect = pymupdf.Rect(drag.left(), drag.top(), drag.right(), drag.bottom())
        strokes, style = self.strokes, self.style
        if self.apply(
            self.tr("Firma"), page, lambda pg: core_annots.add_signature(pg, rect, strokes, style.rgb(), style.width)
        ):
            self.finished.emit()

    def cancel(self):
        self._drag_page = -1
        self._hover = None
        self.update()

    def paint_page(self, painter, page):
        if not self.strokes:
            return
        if self._drag_page == page:
            rect = QRectF(self._start, self._end).normalized()
            if rect.width() < 4:
                rect = self._ghost_rect(self._start)
        elif self._hover is not None and self._hover.page == page and self._drag_page < 0:
            rect = self._ghost_rect(self._hover.point)
        else:
            return
        guide = QPen(theme.color("accent"), 0.8, Qt.PenStyle.DashLine)
        painter.setPen(guide)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(rect)
        color = self.style.qcolor()
        color.setAlphaF(0.75)
        pen = QPen(color, self.style.width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        unit = core_annots.normalize_strokes(self.strokes)
        w_unit = max((p[0] for s in unit for p in s), default=1) or 1
        h_unit = max((p[1] for s in unit for p in s), default=1) or 1
        scale = min(rect.width() / w_unit, rect.height() / h_unit)
        off_x = rect.left() + (rect.width() - w_unit * scale) / 2
        off_y = rect.top() + (rect.height() - h_unit * scale) / 2
        for stroke in unit:
            if not stroke:
                continue
            path = QPainterPath(QPointF(off_x + stroke[0][0] * scale, off_y + stroke[0][1] * scale))
            for x, y in stroke[1:]:
                path.lineTo(QPointF(off_x + x * scale, off_y + y * scale))
            painter.drawPath(path)


class StampTool(AnnotationTool):
    """Sello: clic para ponerlo a su tamaño o arrastrar para darle otro."""

    name = "stamp"
    SCALE = 0.85
    """Tamaño al poner el sello con un clic, respecto a su tamaño natural."""

    def __init__(self, viewer):
        super().__init__(viewer)
        self.choice = StampChoice.load()
        self._hover: PageHit | None = None
        self._drag_page = -1
        self._start = QPointF()
        self._end = QPointF()
        self._ghost: tuple[tuple, QImage] | None = None

    def set_choice(self, choice) -> None:
        self.choice = choice
        self._ghost = None
        self.update()

    def cursor(self):
        return QCursor(Qt.CursorShape.CrossCursor)

    def _natural(self, subtitle: str) -> tuple[float, float]:
        width, height = core_annots.stamp_size(self.choice.label, subtitle)
        return width * self.SCALE, height * self.SCALE

    def _ghost_rect(self, center: QPointF, subtitle: str) -> QRectF:
        width, height = self._natural(subtitle)
        return QRectF(center.x() - width / 2, center.y() - height / 2, width, height)

    def hover(self, event):
        super().hover(event)
        self._hover = self.viewer.hit_test(event.position())
        self.update()

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        self._drag_page = hit.page
        self._start = self._end = hit.point

    def move(self, event):
        if self._drag_page < 0:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is not None and hit.page == self._drag_page:
            self._end = hit.point
            self.update()

    def release(self, event):
        if self._drag_page < 0:
            return
        page = self._drag_page
        self._drag_page = -1
        subtitle = self.choice.subtitle()
        area = QRectF(self._start, self._end).normalized()
        if area.width() * self.viewer.scale < 12 or area.height() * self.viewer.scale < 12:
            area = self._ghost_rect(self._start, subtitle)
        rect = pymupdf.Rect(area.left(), area.top(), area.right(), area.bottom())
        choice = self.choice
        if self.apply(self.tr("Sello"), page, lambda pg: core_annots.add_stamp(
                pg, rect, choice.label, choice.rgb(), subtitle, choice.kind)):
            self.finished.emit()

    def cancel(self):
        self._drag_page = -1
        self._hover = None
        self.update()

    def _ghost_image(self, subtitle: str, height: float) -> QImage:
        dpr = self.viewer.devicePixelRatioF()
        key = (self.choice.label, self.choice.color, subtitle, round(height * self.viewer.scale), dpr)
        if self._ghost is None or self._ghost[0] != key:
            self._ghost = (key, stamp_image(self.choice, height * self.viewer.scale, dpr, subtitle))
        return self._ghost[1]

    def paint_page(self, painter, page):
        subtitle = self.choice.subtitle()
        if self._drag_page == page:
            drag = QRectF(self._start, self._end).normalized()
            if drag.width() * self.viewer.scale < 12:
                rect = self._ghost_rect(self._start, subtitle)
            else:
                width, height = self._natural(subtitle)
                scale = min(drag.width() / width, max(drag.height(), 1) / height)
                rect = QRectF(0, 0, width * scale, height * scale)
                rect.moveCenter(drag.center())
                painter.setPen(QPen(theme.color("accent"), 0.8 / max(self.viewer.scale, 0.1), Qt.PenStyle.DashLine))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(drag)
        elif self._hover is not None and self._hover.page == page and self._drag_page < 0:
            rect = self._ghost_rect(self._hover.point, subtitle)
        else:
            return
        painter.setOpacity(0.75)
        painter.drawImage(rect, self._ghost_image(subtitle, rect.height()))
        painter.setOpacity(1.0)


class MeasureTool(AnnotationTool):
    """Medir a escala: distancia (dos clics o arrastrar), perímetro y área (clic en cada vértice).

    El cursor se ajusta a los extremos de lo dibujado (Alt lo desactiva) y Mayús fija
    ángulos de 45°. En el modo «calibrate» se traza una línea de medida conocida.
    """

    name = "measure"
    calibrated = Signal(float)
    """Longitud en puntos de la línea trazada para calibrar."""
    SNAP_PIXELS = 9.0
    CELL = 12.0

    def __init__(self, viewer):
        super().__init__(viewer)
        self.mode = "distance"
        self.scale_for = lambda page, point: core_measure.Scale()
        self._page = -1
        """Página de la medida en curso."""
        self._hover_page = -1
        self._points: list[QPointF] = []
        self._cursor: QPointF | None = None
        self._snapped: QPointF | None = None
        self._dragging = False
        self._press_pos = QPointF()
        self._grids: dict[int, dict[tuple[int, int], list[tuple[float, float]]]] = {}
        viewer.model.changed.connect(lambda _pages: self._grids.clear())
        viewer.model.aboutToReplace.connect(self._grids.clear)

    @property
    def busy(self) -> bool:
        return bool(self._points)

    def set_mode(self, mode: str) -> None:
        self.cancel()
        self.mode = mode

    def cursor(self):
        return QCursor(Qt.CursorShape.CrossCursor)

    # ---------- ajuste ----------
    def _grid(self, page: int) -> dict:
        grid = self._grids.get(page)
        if grid is None:
            grid = {}
            for x, y in core_measure.snap_targets(self.model.doc[page]):
                grid.setdefault((int(x // self.CELL), int(y // self.CELL)), []).append((x, y))
            self._grids[page] = grid
        return grid

    def _snap(self, page: int, point: QPointF, event) -> QPointF:
        self._snapped = None
        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
            return point
        reach = self.SNAP_PIXELS / max(self.viewer.scale, 0.01)
        grid = self._grid(page)
        cx, cy = int(point.x() // self.CELL), int(point.y() // self.CELL)
        span = int(reach // self.CELL) + 1
        best, best_distance = None, reach
        for gx in range(cx - span, cx + span + 1):
            for gy in range(cy - span, cy + span + 1):
                for x, y in grid.get((gx, gy), ()):
                    distance = math.hypot(x - point.x(), y - point.y())
                    if distance <= best_distance:
                        best, best_distance = (x, y), distance
        if best is None:
            return point
        self._snapped = QPointF(*best)
        return self._snapped

    def _locate(self, event, clamp: bool) -> QPointF | None:
        hit = self.viewer.hit_test(event.position(), clamp=clamp)
        if hit is None or (self._points and hit.page != self._page):
            return None
        self._hover_page = hit.page
        point = self._snap(hit.page, hit.point, event)
        if self._points and self._snapped is None and event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            point = self._constrain(point)
        return point

    def _constrain(self, point: QPointF) -> QPointF:
        last = self._points[-1]
        dx, dy = point.x() - last.x(), point.y() - last.y()
        angle = round(math.atan2(dy, dx) / (math.pi / 4)) * (math.pi / 4)
        length = math.hypot(dx, dy)
        return QPointF(last.x() + length * math.cos(angle), last.y() + length * math.sin(angle))

    # ---------- ratón y teclado ----------
    def _two_points(self) -> bool:
        return self.mode in ("distance", "calibrate")

    def press(self, event):
        if not self._points:
            hit = self.viewer.hit_test(event.position())
            if hit is None:
                return
            self._page = hit.page
        point = self._locate(event, clamp=True)
        if point is None:
            return
        if self.mode == "area" and len(self._points) >= 3 and self._near_first(point):
            self.finish()
            return
        self._points.append(point)
        self._dragging = len(self._points) == 1
        self._press_pos = QPointF(event.position())
        if self._two_points() and len(self._points) == 2:
            self.finish()
            return
        self.update()

    def _near_first(self, point: QPointF) -> bool:
        first = self._points[0]
        return math.hypot(point.x() - first.x(), point.y() - first.y()) * self.viewer.scale <= self.SNAP_PIXELS

    def hover(self, event):
        super().hover(event)
        self._cursor = self._locate(event, clamp=bool(self._points))
        if self._cursor is None:
            self._snapped = None
        self.update()

    def move(self, event):
        self.hover(event)

    def release(self, event):
        moved = (QPointF(event.position()) - self._press_pos).manhattanLength()
        if self._dragging and self._two_points() and moved > DRAG_THRESHOLD * 3 and self._cursor is not None:
            self._points.append(self._cursor)
            self.finish()
        self._dragging = False

    def double_click(self, event):
        if not self._two_points():
            self.finish()

    def key_press(self, event) -> bool:
        if not self._points:
            return False
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self.finish()
            return True
        if event.key() == Qt.Key.Key_Backspace:
            self._points.pop()
            self.update()
            return True
        return False

    # ---------- resultado ----------
    def _clean(self, points: list[QPointF]) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        for p in points:
            if not result or math.hypot(p.x() - result[-1][0], p.y() - result[-1][1]) >= 0.5:
                result.append((p.x(), p.y()))
        return result

    def _scale(self) -> core_measure.Scale:
        first = self._points[0]
        return self.scale_for(self._page, (first.x(), first.y()))

    def _value_text(self, points: list[tuple[float, float]]) -> str:
        scale = self._scale()
        kind = "distance" if self.mode == "calibrate" else self.mode
        value = core_measure.measure(kind, points, scale)
        return format_measure(value, scale.unit, area=kind == "area")

    def finish(self) -> None:
        page, points, mode = self._page, self._clean(self._points), self.mode
        scale = self._scale() if self._points else None
        self.cancel()
        needed = 3 if mode == "area" else 2
        if len(points) < needed or scale is None:
            return
        if mode == "calibrate":
            self.calibrated.emit(core_measure.path_length(points[:2]))
            return
        kind = mode
        label = format_measure(core_measure.measure(kind, points, scale), scale.unit, area=kind == "area")
        style = self.style
        names = {"distance": self.tr("Medir distancia"), "perimeter": self.tr("Medir perímetro"),
                 "area": self.tr("Medir área")}
        self.apply(names[kind], page, lambda pg: core_measure.add_measurement(
            pg, kind, points, scale, label, style.rgb(), style.width))

    def cancel(self):
        self._page = -1
        self._points = []
        self._dragging = False
        self.update()

    def deactivate(self) -> None:
        super().deactivate()
        self._cursor = None
        self._snapped = None

    # ---------- dibujo ----------
    def paint_page(self, painter, page):
        scale = max(self.viewer.scale, 0.01)
        if self._snapped is not None and page == self._hover_page:
            side = 9 / scale
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(theme.color("accent"), 1.6 / scale))
            painter.drawRect(QRectF(self._snapped.x() - side / 2, self._snapped.y() - side / 2, side, side))
        if page != self._page:
            return
        color = self.style.qcolor()
        points = list(self._points) + ([self._cursor] if self._points and self._cursor is not None else [])
        if points:
            pen = QPen(color, max(self.style.width, 1.0 / scale))
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            if self.mode == "area" and len(points) >= 3:
                fill = QColor(color)
                fill.setAlphaF(0.12)
                painter.setBrush(fill)
                path = QPainterPath(points[0])
                for point in points[1:]:
                    path.lineTo(point)
                path.closeSubpath()
                painter.drawPath(path)
            else:
                painter.setBrush(Qt.BrushStyle.NoBrush)
                path = QPainterPath(points[0])
                for point in points[1:]:
                    path.lineTo(point)
                painter.drawPath(path)
            painter.setPen(QPen(color, 1.2 / scale))
            painter.setBrush(QColor("#FFFFFF"))
            for point in self._points:
                painter.drawEllipse(point, 3 / scale, 3 / scale)
        if len(points) >= 2 and self._cursor is not None:
            self._paint_readout(painter, self._value_text(self._clean(points)))

    def _paint_readout(self, painter: QPainter, text: str) -> None:
        """La cifra junto al cursor, a tamaño de pantalla (no crece con el zoom)."""
        anchor = painter.transform().map(self._cursor)
        painter.save()
        painter.resetTransform()
        font = QFont(painter.font())
        font.setPointSizeF(9.5)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        box = metrics.boundingRect(text).adjusted(-8, -4, 8, 4)
        box.moveTopLeft(QPointF(anchor.x() + 14, anchor.y() + 14))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.color("surface"))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(theme.color("text"))
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class EraserTool(AnnotationTool):
    """Borra la anotación bajo el cursor (resaltada al pasar por encima)."""

    name = "eraser"

    def __init__(self, viewer):
        super().__init__(viewer)
        self._hover: tuple[int, int, QRectF] | None = None

    def cursor(self):
        return make_cursor("eraser", 4, 18)

    def _find(self, event) -> tuple[int, int, QRectF] | None:
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return None
        page = self.model.doc[hit.page]
        tolerance = 4 / max(self.viewer.scale, 0.1)
        xref = core_annots.annotation_at(page, (hit.point.x(), hit.point.y()), tolerance)
        if xref is None:
            return None
        for info in core_annots.list_annotations(page):
            if info.xref == xref:
                return hit.page, xref, to_rect(info.rect)
        return None

    def hover(self, event):
        super().hover(event)
        found = self._find(event)
        if found != self._hover:
            self._hover = found
            self.update()

    def release(self, event):
        found = self._find(event)
        self._hover = None
        if found is None:
            return
        page, xref, _ = found
        from ..core.comments import delete_comment

        self.apply(self.tr("Borrar anotación"), page, lambda pg: delete_comment(pg, xref))

    def cancel(self):
        self._hover = None
        self.update()

    def paint_page(self, painter, page):
        if self._hover is None or self._hover[0] != page:
            return
        color = theme.color("accent")
        pen = QPen(color, 1.2 / max(self.viewer.scale, 0.1) * 1.5, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        fill = QColor(color)
        fill.setAlphaF(0.08)
        painter.setBrush(fill)
        painter.drawRect(self._hover[2].adjusted(-2, -2, 2, 2))


class RedactTool(AnnotationTool):
    """Censura: arrastra sobre una zona para eliminar su contenido de forma definitiva."""

    name = "redact"

    def __init__(self, viewer):
        super().__init__(viewer, "redact")
        self._page = -1
        self._start = QPointF()
        self._end = QPointF()

    def cursor(self):
        return QCursor(Qt.CursorShape.CrossCursor)

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        self._page = hit.page
        self._start = self._end = hit.point

    def move(self, event):
        if self._page < 0:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is not None and hit.page == self._page:
            self._end = hit.point
            self.update()

    def release(self, event):
        if self._page < 0:
            return
        page = self._page
        area = QRectF(self._start, self._end).normalized()
        self.cancel()
        if area.width() * self.viewer.scale < 4 or area.height() * self.viewer.scale < 4:
            return
        rect = pymupdf.Rect(area.left(), area.top(), area.right(), area.bottom())
        # Lo censurado no puede quedar en una revisión anterior del archivo: se guarda entero.
        if self.apply(self.tr("Censurar"), page, lambda pg: core_annots.redact_areas(pg, [rect]), rewrite=True):
            self.finished.emit()

    def cancel(self):
        self._page = -1
        self.update()

    def paint_page(self, painter, page):
        if page != self._page:
            return
        area = QRectF(self._start, self._end).normalized()
        fill = QColor(0, 0, 0)
        fill.setAlphaF(0.55)
        painter.setBrush(fill)
        painter.setPen(QPen(theme.color("accent"), 1.0 / max(self.viewer.scale, 0.1) * 1.5, Qt.PenStyle.DashLine))
        painter.drawRect(area)


class PlaceRectTool(ViewerTool):
    """Dibuja un recuadro en una página y lo devuelve (por ejemplo, para colocar un modelo 3D)."""

    name = "place"
    placed = Signal(int, object)
    """(página, rectángulo en coordenadas visibles de la página)."""

    def __init__(self, viewer):
        super().__init__(viewer)
        self._page = -1
        self._start = QPointF()
        self._end = QPointF()

    def cursor(self):
        return QCursor(Qt.CursorShape.CrossCursor)

    def hover(self, event):
        self.viewer.viewport().setCursor(self.cursor())

    def press(self, event):
        hit = self.viewer.hit_test(event.position())
        if hit is None:
            return
        self._page = hit.page
        self._start = self._end = hit.point

    def move(self, event):
        if self._page < 0:
            return
        hit = self.viewer.hit_test(event.position(), clamp=True)
        if hit is not None and hit.page == self._page:
            self._end = hit.point
            self.update()

    def release(self, event):
        if self._page < 0:
            return
        page = self._page
        area = QRectF(self._start, self._end).normalized()
        self.cancel()
        size = self.viewer.model.page_size(page)
        if area.width() * self.viewer.scale < 12 or area.height() * self.viewer.scale < 12:
            # Un clic: recuadro de la mitad del ancho de la página, centrado donde se ha pulsado.
            width = size.width() * 0.5
            height = width * 0.75
            left = min(max(0.0, area.center().x() - width / 2), size.width() - width)
            top = min(max(0.0, area.center().y() - height / 2), size.height() - height)
            area = QRectF(left, top, width, height)
        self.placed.emit(page, pymupdf.Rect(area.left(), area.top(), area.right(), area.bottom()))
        self.finished.emit()

    def cancel(self):
        self._page = -1
        self.update()

    def paint_page(self, painter, page):
        if page != self._page:
            return
        fill = QColor(theme.color("accent"))
        fill.setAlphaF(0.10)
        painter.setBrush(fill)
        painter.setPen(QPen(theme.color("accent"), 1.0 / max(self.viewer.scale, 0.1) * 1.5, Qt.PenStyle.DashLine))
        painter.drawRect(QRectF(self._start, self._end).normalized())


TOOL_CLASSES = {
    "select": SelectTool,
    "hand": HandTool,
    "pen": PenTool,
    "highlight": lambda v: MarkupTool(v, "highlight"),
    "underline": lambda v: MarkupTool(v, "underline"),
    "strikeout": lambda v: MarkupTool(v, "strikeout"),
    "rect": lambda v: ShapeTool(v, "rect"),
    "ellipse": lambda v: ShapeTool(v, "ellipse"),
    "line": lambda v: ShapeTool(v, "line"),
    "arrow": lambda v: ShapeTool(v, "arrow"),
    "polygon": PolygonTool,
    "text": TextTool,
    "note": NoteTool,
    "signature": SignatureTool,
    "stamp": StampTool,
    "measure": MeasureTool,
    "eraser": EraserTool,
    "redact": RedactTool,
}

STYLED_TOOLS = ("pen", "highlight", "underline", "strikeout", "rect", "ellipse", "line", "arrow", "polygon", "text",
                "note", "signature", "measure")
WIDTH_TOOLS = ("pen", "rect", "ellipse", "line", "arrow", "polygon", "signature")


def create_tool(name: str, viewer: PdfViewer) -> ViewerTool:
    return TOOL_CLASSES[name](viewer)

