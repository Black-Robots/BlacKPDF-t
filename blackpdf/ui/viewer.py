"""Visor de PDF con scroll continuo, zoom y render perezoso por teselas."""

from __future__ import annotations

import math
from typing import NamedTuple

import pymupdf
from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    QSizeF,
    Qt,
    QTimer,
    QUrl,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QDesktopServices,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QNativeGestureEvent,
    QPainter,
    QWheelEvent,
)
from PySide6.QtWidgets import QAbstractScrollArea, QMessageBox

from ..core import navigation, text as core_text
from . import theme
from .document import PdfDocument
from .render import TILE, PageRenderer, TileKey

PX_PER_PT = 96 / 72
GAP = 16
MARGIN = 24
MIN_ZOOM = 0.1
MAX_ZOOM = 6.4
ZOOM_STEPS = (0.1, 0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0, 5.0, 6.4)


class PageHit(NamedTuple):
    page: int
    point: QPointF
    """Punto en coordenadas visibles de la página (puntos PDF)."""


def to_rect(rect: pymupdf.Rect) -> QRectF:
    return QRectF(rect.x0, rect.y0, rect.width, rect.height)


class PdfViewer(QAbstractScrollArea):
    zoomChanged = Signal(float)
    currentPageChanged = Signal(int)
    fitModeChanged = Signal(str)
    selectionChanged = Signal(bool)
    contextMenuRequested = Signal(object, object)
    """(posición global, PageHit o None)."""

    def __init__(self, model: PdfDocument, renderer: PageRenderer, parent=None):
        super().__init__(parent)
        self.model = model
        self.renderer = renderer
        self._zoom = 1.0
        self._fit = "auto"
        self._top_inset = 0
        self._night = False
        self._rects: list[QRectF] = []
        self._content = QSizeF(0, 0)
        self._current = 0
        self._words: dict[int, list[core_text.Word]] = {}
        self._links: dict[int, list[navigation.PageLink]] = {}
        self._search_hits: dict[int, list[QRectF]] = {}
        self._current_hit: tuple[int, int] | None = None
        self._selection_page = -1
        self._selection: list[core_text.Word] = []
        self._tool = None
        self._pan_origin: QPointF | None = None
        self._pan_scroll = (0, 0)

        self.setFrameShape(QAbstractScrollArea.Shape.NoFrame)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.viewport().setMouseTracking(True)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setAttribute(Qt.WidgetAttribute.WA_AcceptTouchEvents)
        self.viewport().grabGesture(Qt.GestureType.PinchGesture)
        self.verticalScrollBar().setSingleStep(48)
        self.horizontalScrollBar().setSingleStep(48)

        self._scroll_anim = QVariantAnimation(self)
        self._scroll_anim.setDuration(170)
        self._scroll_anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_anim.valueChanged.connect(lambda v: self.verticalScrollBar().setValue(int(v)))
        self._scroll_target = 0.0

        self._refit_timer = QTimer(self)
        self._refit_timer.setSingleShot(True)
        self._refit_timer.setInterval(30)
        self._refit_timer.timeout.connect(self._apply_fit)

        model.changed.connect(self._on_model_changed)
        renderer.tileReady.connect(self._on_page_rendered)
        renderer.thumbReady.connect(self._on_page_rendered)
        theme.manager().changed.connect(self._on_theme_changed)
        self._relayout()

    # ======================================================================
    # Propiedades
    # ======================================================================
    @property
    def zoom(self) -> float:
        return self._zoom

    @property
    def fit_mode(self) -> str:
        return self._fit

    @property
    def scale(self) -> float:
        """Píxeles lógicos por punto PDF."""
        return self._zoom * PX_PER_PT

    @property
    def current_page(self) -> int:
        return self._current

    @property
    def night_mode(self) -> bool:
        return self._night

    def set_night_mode(self, enabled: bool) -> None:
        if enabled != self._night:
            self._night = enabled
            self.viewport().update()

    def set_top_inset(self, inset: int) -> None:
        """Espacio libre arriba para que la barra flotante no tape la primera página."""
        if inset != self._top_inset:
            self._top_inset = inset
            self._relayout()
            self.viewport().update()

    def set_tool(self, tool) -> None:
        if self._tool is not None:
            self._tool.deactivate()
        self._tool = tool
        if tool is not None:
            tool.activate()
            self.viewport().setCursor(tool.cursor())
        self.viewport().update()

    @property
    def tool(self):
        return self._tool

    # ======================================================================
    # Geometría
    # ======================================================================
    def _offset(self) -> QPointF:
        return QPointF(self.horizontalScrollBar().value(), self.verticalScrollBar().value())

    def _relayout(self) -> None:
        sizes = self.model.page_sizes()
        s = self.scale
        widest = max((size.width() for size in sizes), default=0) * s
        content_w = max(float(self.viewport().width()), widest + 2 * MARGIN)
        y = float(MARGIN + self._top_inset)
        rects = []
        for size in sizes:
            w, h = size.width() * s, size.height() * s
            rects.append(QRectF((content_w - w) / 2, y, w, h))
            y += h + GAP
        self._rects = rects
        self._content = QSizeF(content_w, y - GAP + MARGIN if rects else 0)
        self._update_scrollbars()

    def _update_scrollbars(self) -> None:
        vw, vh = self.viewport().width(), self.viewport().height()
        vbar, hbar = self.verticalScrollBar(), self.horizontalScrollBar()
        vbar.setPageStep(vh)
        hbar.setPageStep(vw)
        vbar.setRange(0, max(0, int(math.ceil(self._content.height() - vh))))
        hbar.setRange(0, max(0, int(math.ceil(self._content.width() - vw))))

    def page_rect(self, index: int) -> QRectF:
        """Rectángulo de la página en coordenadas del viewport."""
        return self._rects[index].translated(-self._offset())

    def visible_pages(self) -> list[int]:
        if not self._rects:
            return []
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        lo, hi = 0, len(self._rects)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._rects[mid].bottom() < top:
                lo = mid + 1
            else:
                hi = mid
        result = []
        for index in range(lo, len(self._rects)):
            rect = self._rects[index]
            if rect.top() > bottom:
                break
            result.append(index)
        return result

    def hit_test(self, pos: QPointF, clamp: bool = False) -> PageHit | None:
        """Página y punto (en pt) bajo una posición del viewport."""
        content = QPointF(pos) + self._offset()
        candidates = self.visible_pages() or range(len(self._rects))
        best = None
        best_distance = float("inf")
        for index in candidates:
            rect = self._rects[index]
            if rect.contains(content):
                best = index
                break
            if clamp:
                dy = max(rect.top() - content.y(), 0, content.y() - rect.bottom())
                dx = max(rect.left() - content.x(), 0, content.x() - rect.right())
                distance = dx + dy
                if distance < best_distance:
                    best, best_distance = index, distance
        if best is None:
            return None
        rect = self._rects[best]
        s = self.scale
        x = (content.x() - rect.left()) / s
        y = (content.y() - rect.top()) / s
        if clamp:
            size = self.model.page_size(best)
            x = min(max(x, 0.0), size.width())
            y = min(max(y, 0.0), size.height())
        return PageHit(best, QPointF(x, y))

    def page_to_viewport(self, page: int, point: QPointF) -> QPointF:
        rect = self.page_rect(page)
        return QPointF(rect.left() + point.x() * self.scale, rect.top() + point.y() * self.scale)

    def page_rect_to_viewport(self, page: int, rect: QRectF) -> QRectF:
        top_left = self.page_to_viewport(page, rect.topLeft())
        return QRectF(top_left, QSizeF(rect.width() * self.scale, rect.height() * self.scale))

    # ======================================================================
    # Zoom y navegación
    # ======================================================================
    def set_zoom(self, zoom: float, anchor: QPointF | None = None, keep_fit: bool = False) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if not keep_fit and self._fit:
            self._fit = ""
            self.fitModeChanged.emit("")
        if abs(zoom - self._zoom) < 1e-4:
            return
        if anchor is None:
            anchor = QPointF(self.viewport().width() / 2, self.viewport().height() / 3)
        hit = self.hit_test(anchor, clamp=True)
        self._zoom = zoom
        self._relayout()
        if hit is not None:
            rect = self._rects[hit.page]
            target = QPointF(rect.left() + hit.point.x() * self.scale, rect.top() + hit.point.y() * self.scale)
            self.horizontalScrollBar().setValue(int(round(target.x() - anchor.x())))
            self.verticalScrollBar().setValue(int(round(target.y() - anchor.y())))
        self.renderer.clear_tiles()
        self.zoomChanged.emit(self._zoom)
        self._update_current_page()
        self._tool_viewport_changed()
        self.viewport().update()

    def zoom_in(self) -> None:
        self.set_zoom(next((z for z in ZOOM_STEPS if z > self._zoom + 1e-3), MAX_ZOOM))

    def zoom_out(self) -> None:
        self.set_zoom(next((z for z in reversed(ZOOM_STEPS) if z < self._zoom - 1e-3), MIN_ZOOM))

    def set_fit_mode(self, mode: str) -> None:
        """"auto" (ancho, hasta el 100 %), "width", "page" o "" (zoom libre)."""
        self._fit = mode
        self.fitModeChanged.emit(mode)
        self._apply_fit()

    def _fit_zoom(self, mode: str) -> float | None:
        if not self._rects:
            return None
        size = self.model.page_size(min(self._current, self.model.page_count - 1))
        widest = max(s.width() for s in self.model.page_sizes())
        avail_w = max(50, self.viewport().width() - 2 * MARGIN - (0 if self.verticalScrollBar().isVisible() else 12))
        avail_h = max(50, self.viewport().height() - 2 * MARGIN - self._top_inset)
        by_width = avail_w / (widest * PX_PER_PT)
        if mode == "auto":
            return min(by_width, 1.0)
        if mode == "width":
            return min(by_width, 4.0)
        if mode == "page":
            return min(avail_w / (size.width() * PX_PER_PT), avail_h / (size.height() * PX_PER_PT))
        return None

    def _apply_fit(self) -> None:
        zoom = self._fit_zoom(self._fit)
        if zoom is None:
            return
        page = self._current
        self._zoom_keep_page(zoom)
        if self._fit == "page":
            self.go_to_page(page, smooth=False)

    def _zoom_keep_page(self, zoom: float) -> None:
        zoom = max(MIN_ZOOM, min(MAX_ZOOM, zoom))
        if abs(zoom - self._zoom) < 1e-4:
            self._relayout()
            self.viewport().update()
            return
        anchor = QPointF(self.viewport().width() / 2, 0)
        hit = self.hit_test(anchor, clamp=True)
        self._zoom = zoom
        self._relayout()
        if hit is not None:
            rect = self._rects[hit.page]
            self.verticalScrollBar().setValue(int(round(rect.top() + hit.point.y() * self.scale)))
        self.renderer.clear_tiles()
        self.zoomChanged.emit(self._zoom)
        self._tool_viewport_changed()
        self.viewport().update()

    def go_to_page(self, index: int, y_pt: float | None = None, smooth: bool = True) -> None:
        if not self._rects:
            return
        index = max(0, min(index, len(self._rects) - 1))
        rect = self._rects[index]
        target = rect.top() - MARGIN / 2 - self._top_inset
        if y_pt is not None:
            target = rect.top() + y_pt * self.scale - self.viewport().height() / 4
        self._scroll_to(target, smooth)
        self._current = index
        self.currentPageChanged.emit(index)

    def ensure_visible(self, page: int, rect_pt: QRectF) -> None:
        rect = self.page_rect_to_viewport(page, rect_pt)
        view = QRectF(self.viewport().rect()).adjusted(24, 24, -24, -24)
        if view.contains(rect):
            return
        offset = self._offset()
        x = rect.center().x() + offset.x() - self.viewport().width() / 2
        y = rect.center().y() + offset.y() - self.viewport().height() / 2
        self.horizontalScrollBar().setValue(int(x))
        self._scroll_to(y, smooth=True)

    def _scroll_to(self, value: float, smooth: bool) -> None:
        bar = self.verticalScrollBar()
        value = max(bar.minimum(), min(bar.maximum(), value))
        self._scroll_anim.stop()
        if not smooth:
            bar.setValue(int(value))
            return
        self._scroll_anim.setStartValue(float(bar.value()))
        self._scroll_anim.setEndValue(float(value))
        self._scroll_target = value
        self._scroll_anim.start()

    def next_page(self) -> None:
        self.go_to_page(self._current + 1)

    def previous_page(self) -> None:
        self.go_to_page(self._current - 1)

    def _update_current_page(self) -> None:
        pages = self.visible_pages()
        if not pages:
            return
        top = self.verticalScrollBar().value()
        bottom = top + self.viewport().height()
        if self.verticalScrollBar().value() >= self.verticalScrollBar().maximum() > 0:
            best = pages[-1]
        else:
            best = max(
                pages,
                key=lambda i: min(self._rects[i].bottom(), bottom) - max(self._rects[i].top(), top),
            )
        if best != self._current:
            self._current = best
            self.currentPageChanged.emit(best)

    # ======================================================================
    # Datos auxiliares por página
    # ======================================================================
    def words(self, page: int) -> list[core_text.Word]:
        if page not in self._words:
            try:
                self._words[page] = core_text.page_words(self.model.doc[page])
            except Exception:
                self._words[page] = []
        return self._words[page]

    def links(self, page: int) -> list[navigation.PageLink]:
        if page not in self._links:
            try:
                self._links[page] = navigation.page_links(self.model.doc[page])
            except Exception:
                self._links[page] = []
        return self._links[page]

    def link_at(self, hit: PageHit | None) -> navigation.PageLink | None:
        if hit is None:
            return None
        point = pymupdf.Point(hit.point.x(), hit.point.y())
        for link in self.links(hit.page):
            if point in link.rect:
                return link
        return None

    def follow_link(self, link: navigation.PageLink) -> None:
        if link.page >= 0:
            self.go_to_page(link.page)
            return
        url = QUrl(link.uri)
        if url.scheme().lower() not in ("http", "https", "mailto"):
            return
        answer = QMessageBox.question(
            self,
            self.tr("Abrir enlace"),
            self.tr("¿Abrir este enlace en el navegador?\n\n{0}").format(link.uri),
        )
        if answer == QMessageBox.StandardButton.Yes:
            QDesktopServices.openUrl(url)

    # ======================================================================
    # Búsqueda
    # ======================================================================
    def set_search_hits(self, hits: dict[int, list[pymupdf.Rect]]) -> None:
        self._search_hits = {page: [to_rect(r) for r in rects] for page, rects in hits.items()}
        self.viewport().update()

    def add_search_hits(self, page: int, rects: list[pymupdf.Rect]) -> None:
        self._search_hits[page] = [to_rect(r) for r in rects]
        if page in self.visible_pages():
            self.viewport().update()

    def set_current_hit(self, page: int, index: int) -> None:
        self._current_hit = (page, index)
        rects = self._search_hits.get(page, [])
        if 0 <= index < len(rects):
            self.ensure_visible(page, rects[index])
        self.viewport().update()

    def clear_search(self) -> None:
        self._search_hits = {}
        self._current_hit = None
        self.viewport().update()

    # ======================================================================
    # Selección de texto
    # ======================================================================
    def set_selection(self, page: int, words: list[core_text.Word]) -> None:
        had = bool(self._selection)
        self._selection_page = page
        self._selection = words
        if bool(words) != had:
            self.selectionChanged.emit(bool(words))
        self.viewport().update()

    def clear_selection(self) -> None:
        if self._selection:
            self.set_selection(-1, [])

    def selected_text(self) -> str:
        return core_text.words_text(self._selection)

    def selection(self) -> tuple[int, list[core_text.Word]]:
        return self._selection_page, self._selection

    def selection_rects(self) -> list[pymupdf.Rect]:
        return core_text.line_rects(self._selection)

    def copy_selection(self) -> bool:
        text = self.selected_text()
        if text:
            QGuiApplication.clipboard().setText(text)
            return True
        return False

    def select_page_text(self, page: int | None = None) -> None:
        page = self._current if page is None else page
        self.set_selection(page, list(self.words(page)))

    # ======================================================================
    # Eventos del modelo
    # ======================================================================
    def _on_model_changed(self, pages) -> None:
        if pages is None:
            self._words.clear()
            self._links.clear()
            self._search_hits.clear()
            self._current_hit = None
            self.clear_selection()
        else:
            for page in pages:
                self._words.pop(page, None)
                self._links.pop(page, None)
        self._relayout()
        if self._fit:
            self._refit_timer.start()
        if self._current >= self.model.page_count:
            self._current = max(0, self.model.page_count - 1)
        self._update_current_page()
        self.viewport().update()

    def _on_page_rendered(self, page: int) -> None:
        if page in self.visible_pages():
            self.viewport().update()

    def _on_theme_changed(self) -> None:
        if self._night:
            self.renderer.invalidate(None)
        self.viewport().update()

    def _tool_viewport_changed(self) -> None:
        if self._tool is not None:
            self._tool.viewport_changed()

    # ======================================================================
    # Eventos Qt
    # ======================================================================
    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._relayout()
        if self._fit:
            self._refit_timer.start()
        self._tool_viewport_changed()

    def scrollContentsBy(self, dx: int, dy: int) -> None:
        self._update_current_page()
        self._tool_viewport_changed()
        self.viewport().update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self.viewport())
        p = theme.current()
        painter.fillRect(event.rect(), p.qcolor("viewer"))
        if not self._rects:
            return
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        dpr = self.viewport().devicePixelRatioF()
        self.renderer.set_thumbnail_dpr(dpr)
        device_scale = self.scale * dpr
        scale_key = PageRenderer.scale_key(device_scale)
        offset = self._offset()
        view = QRectF(self.viewport().rect())
        center = view.center()
        requests: list[tuple[float, TileKey]] = []
        page_color = p.qcolor("night_page") if self._night else QColor("#FFFFFF")
        shadow = p.qcolor("page_shadow")

        for index in self.visible_pages():
            rect = self._rects[index].translated(-offset)
            # Alinear a píxeles de dispositivo para que las teselas no se emborronen.
            rect = QRectF(round(rect.x() * dpr) / dpr, round(rect.y() * dpr) / dpr, rect.width(), rect.height())
            self._paint_shadow(painter, rect, shadow)
            painter.fillRect(rect, page_color)

            thumb = self.renderer.thumbnail(index, self._night)
            if thumb is None:
                self.renderer.request_thumbnail(index, self._night, priority=True)
            visible = rect.intersected(view)
            if visible.isEmpty():
                continue
            local_x0 = (visible.left() - rect.left()) * dpr
            local_y0 = (visible.top() - rect.top()) * dpr
            local_x1 = (visible.right() - rect.left()) * dpr
            local_y1 = (visible.bottom() - rect.top()) * dpr
            tx0, ty0 = int(local_x0 // TILE), int(local_y0 // TILE)
            tx1, ty1 = int(max(local_x1 - 1, 0) // TILE), int(max(local_y1 - 1, 0) // TILE)
            missing = False
            tiles = []
            for ty in range(ty0, ty1 + 1):
                for tx in range(tx0, tx1 + 1):
                    key = TileKey(index, scale_key, tx, ty, self._night)
                    tile = self.renderer.tile(key)
                    if tile is None:
                        missing = True
                        tile_center = QPointF(
                            rect.left() + (tx + 0.5) * TILE / dpr, rect.top() + (ty + 0.5) * TILE / dpr
                        )
                        distance = abs(tile_center.y() - center.y()) + abs(tile_center.x() - center.x()) * 0.2
                        requests.append((distance, key))
                    else:
                        tiles.append(tile)
            if missing and thumb is not None:
                painter.drawImage(rect, thumb)
            for tile in tiles:
                target = QRectF(
                    rect.left() + tile.rect.x() / dpr,
                    rect.top() + tile.rect.y() / dpr,
                    tile.rect.width() / dpr,
                    tile.rect.height() / dpr,
                )
                painter.drawImage(target, tile.image)
            self._paint_overlays(painter, index, rect)

        requests.sort(key=lambda item: item[0])
        self.renderer.request_tiles([(key, device_scale) for _, key in requests])

    def _paint_shadow(self, painter: QPainter, rect: QRectF, color: QColor) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        base_alpha = color.alphaF()
        for spread, alpha in ((1.0, 0.45), (3.0, 0.22), (6.0, 0.10)):
            shade = QColor(color)
            shade.setAlphaF(base_alpha * alpha)
            painter.setBrush(shade)
            painter.drawRoundedRect(rect.adjusted(-spread, -spread + 1, spread, spread + 2), spread + 1, spread + 1)
        painter.restore()

    def _paint_overlays(self, painter: QPainter, index: int, rect: QRectF) -> None:
        p = theme.current()
        painter.save()
        painter.translate(rect.topLeft())
        painter.scale(self.scale, self.scale)
        painter.setPen(Qt.PenStyle.NoPen)
        hits = self._search_hits.get(index)
        if hits:
            hit_color = p.qcolor("search_hit")
            current_color = p.qcolor("search_current")
            for position, hit in enumerate(hits):
                is_current = self._current_hit == (index, position)
                painter.setBrush(current_color if is_current else hit_color)
                painter.drawRect(hit.adjusted(-0.5, -0.5, 0.5, 0.5))
        if self._selection and self._selection_page == index:
            painter.setBrush(p.qcolor("selection"))
            for line in core_text.line_rects(self._selection):
                painter.drawRect(to_rect(line).adjusted(-0.5, -0.5, 0.5, 0.5))
        if self._tool is not None:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._tool.paint_page(painter, index)
        painter.restore()

    # ---------- ratón ----------
    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button() == Qt.MouseButton.MiddleButton:
            self._start_pan(event.position())
            return
        if event.button() == Qt.MouseButton.RightButton:
            return
        if self._tool is not None:
            self._tool.press(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._pan_origin is not None:
            self._pan_to(event.position())
            return
        if self._tool is not None:
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._tool.move(event)
                self._autoscroll(event.position())
            else:
                self._tool.hover(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.MiddleButton and self._pan_origin is not None:
            self._pan_origin = None
            self.viewport().setCursor(self._tool.cursor() if self._tool else Qt.CursorShape.ArrowCursor)
            return
        if event.button() == Qt.MouseButton.LeftButton and self._tool is not None:
            self._tool.release(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._tool is not None:
            self._tool.double_click(event)

    def contextMenuEvent(self, event) -> None:
        hit = self.hit_test(QPointF(event.pos()))
        self.contextMenuRequested.emit(event.globalPos(), hit)

    def _start_pan(self, pos: QPointF) -> None:
        self._pan_origin = QPointF(pos)
        self._pan_scroll = (self.horizontalScrollBar().value(), self.verticalScrollBar().value())
        self.viewport().setCursor(Qt.CursorShape.ClosedHandCursor)

    def _pan_to(self, pos: QPointF) -> None:
        delta = pos - self._pan_origin
        self.horizontalScrollBar().setValue(int(self._pan_scroll[0] - delta.x()))
        self.verticalScrollBar().setValue(int(self._pan_scroll[1] - delta.y()))

    def start_pan(self, pos: QPointF) -> None:
        self._start_pan(pos)

    def pan_to(self, pos: QPointF) -> None:
        if self._pan_origin is not None:
            self._pan_to(pos)

    def end_pan(self) -> None:
        self._pan_origin = None

    def _autoscroll(self, pos: QPointF) -> None:
        margin = 24
        height = self.viewport().height()
        bar = self.verticalScrollBar()
        if pos.y() < margin:
            bar.setValue(bar.value() - int((margin - pos.y()) * 0.8) - 2)
        elif pos.y() > height - margin:
            bar.setValue(bar.value() + int((pos.y() - height + margin) * 0.8) + 2)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            steps = event.angleDelta().y() / 120.0
            if steps:
                self.set_zoom(self._zoom * (1.12 ** steps), anchor=event.position())
            event.accept()
            return
        if event.pixelDelta().isNull() and event.angleDelta().y() and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            bar = self.verticalScrollBar()
            base = self._scroll_target if self._scroll_anim.state() == QVariantAnimation.State.Running else bar.value()
            step = -event.angleDelta().y() / 120.0 * 110
            target = max(bar.minimum(), min(bar.maximum(), base + step))
            self._scroll_anim.stop()
            self._scroll_anim.setStartValue(float(bar.value()))
            self._scroll_anim.setEndValue(float(target))
            self._scroll_target = target
            self._scroll_anim.start()
            event.accept()
            return
        super().wheelEvent(event)

    def event(self, event) -> bool:
        if isinstance(event, QNativeGestureEvent) and event.gestureType() == Qt.NativeGestureType.ZoomNativeGesture:
            self.set_zoom(self._zoom * (1.0 + event.value()), anchor=self.viewport().mapFromGlobal(event.globalPosition().toPoint()).toPointF())
            return True
        return super().event(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if self._tool is not None and self._tool.key_press(event):
            return
        key = event.key()
        bar = self.verticalScrollBar()
        if key in (Qt.Key.Key_PageDown, Qt.Key.Key_Space) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._scroll_to(bar.value() + bar.pageStep() * 0.9, True)
        elif key == Qt.Key.Key_PageUp or (key == Qt.Key.Key_Space and event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            self._scroll_to(bar.value() - bar.pageStep() * 0.9, True)
        elif key == Qt.Key.Key_Home and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.go_to_page(0)
        elif key == Qt.Key.Key_End and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.go_to_page(self.model.page_count - 1)
        elif key == Qt.Key.Key_Escape:
            self.clear_selection()
            if self._tool is not None:
                self._tool.cancel()
        elif event.matches(QKeySequence.StandardKey.Copy):
            self.copy_selection()
        else:
            super().keyPressEvent(event)
